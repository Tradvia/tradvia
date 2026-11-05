<!-- tradvia-translation source="docs/adr/0009-programmatic-activity-api.md" source-sha256="2a8ad7e4c391d3ab084fd0b289e388df174b0179e529640eb752e4bfe466d1cf" -->
# ADR 0009: API programática para atividades (tokens de API)

- Status: Aceita
- Data: 2026-10-24
- Issue: #77 (documentada em [#76](https://github.com/Tradvia/tradvia/issues/76))

## Contexto

Toda atividade no Tradvia começa como uma issue do GitHub, passa pelo planner e
roda depois que uma pessoa aprova o plano. A issue #77 pede uma segunda porta de entrada, com duas
direções possíveis:

- **(a) Tarefa rápida**: uma tarefa avulsa sem o ciclo de plano/aprovação.
- **(b) Disparo programático**: uma API/CLI para criar atividades sem o painel web.

## Decisão

Construir a **(b)**. A direção (a) continua **Planned**.

- A (b) não acrescenta nenhuma semântica de execução nova: um card criado pela API é o mesmo
  card, continua passando pelo planner e pelo ponto de aprovação do plano, e todas
  as regras da ADR 0004 e da ADR 0008 continuam valendo.
- A (a) criaria um caminho que roda sem plano e sem aprovação. Isso
  muda o modelo de segurança do plano de controle (ADR 0003) e precisa de uma
  decisão própria sobre o que é uma tarefa "pequena e bem definida", quem pode rodá-la e com
  qual orçamento. Isso não é decidido aqui.

### Tokens de API

- Um token é criado por uma pessoa conectada (cookie de sessão + CSRF, como na ADR 0008) com
  `POST /api/projects/{owner}/{repo}/tokens` e revogado com
  `DELETE /api/projects/{owner}/{repo}/tokens/{id}`. Tokens bearer não podem gerenciar tokens.
- **Escopo**: um projeto, um papel (`read`..`admin`, padrão `triage`) e uma
  validade (padrão 30 dias, no máximo 90).
- **Um token nunca passa do dono dele**: a criação é recusada se o papel for maior que
  o papel do dono no projeto, e em cada requisição o papel efetivo é o
  menor entre o papel do token e o papel *atual* do dono no GitHub. Perder o papel
  no GitHub tira o poder do token na hora.
- **Armazenamento**: a tabela `api_tokens` guarda só o SHA-256 do token
  (`auth.hash_token`), o papel, o projeto, `expires_at` e `revoked_at`. O token
  bruto (`tvt_` + 256 bits aleatórios) é mostrado uma única vez, na criação.
- **Identidade**: a linha também guarda o token de usuário do GitHub do dono, criptografado com a
  mesma chave Fernet das sessões (ADR 0008). Por isso as ações rodam com a identidade
  do dono no GitHub, nunca com o `GITHUB_TOKEN` do servidor. Uma consequência é que um
  token para de funcionar quando o token de usuário do GitHub por trás dele expira ou é revogado;
  o dono então cria um token novo depois de entrar de novo.
- **Requisição**: `Authorization: Bearer <token>` só é lido quando não há cookie
  de sessão. Uma requisição com cookie sempre segue as regras do cookie, inclusive o CSRF. A
  verificação de Origin/CSRF só é pulada em requisições bearer, que não levam credenciais
  automáticas. Tokens desconhecidos, expirados ou revogados respondem 401.
- **Ações permitidas**: só `create_card` e `start_card` (e ler a auditoria do
  card). Aprovar planos, fazer merge, deploys e mudanças no backlog continuam só no painel.
  A verificação de papel existente (`policy.py`, `.tradvia.yml`) continua decidindo cada uma.
- **Auditoria**: `audit_log` ganha uma coluna `via`. As entradas disparadas por token levam o
  dono como `actor_login` e `via = api_token`, então aparecem em
  `GET .../cards/{n}/audit`. Criar e revogar tokens também é auditado.
- **Idempotência**: `X-Tradvia-Idempotency-Key` funciona como no painel (mesmo login,
  mesma chave, 5 s).
- **CLI**: `tradvia token create|revoke` e `tradvia card create` chamam esta API HTTP.

## Consequências

- Um token vazado pode criar e iniciar cards num projeto até expirar ou ser
  revogado, mas não pode aprovar um plano, fazer merge nem deploy.
- Trocar `TRADVIA_SECRET_KEY` invalida os tokens, assim como as sessões.
- Tarefas rápidas sem plano continuam **Planned**; veja
  [docs/programmatic-api.md](../programmatic-api.md).
- Status: Shipped (autenticação por token, criação e início de cards). Exige a mesma configuração do App que
  a ADR 0008.
