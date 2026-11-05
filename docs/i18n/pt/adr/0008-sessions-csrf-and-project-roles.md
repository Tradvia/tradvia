<!-- tradvia-translation source="docs/adr/0008-sessions-csrf-and-project-roles.md" source-sha256="07986d272fca603782d47db1a1adfd7cfeaa54b93171e8007ee6cc32ec727cab" -->
# ADR 0008: Sessões, CSRF e papéis por projeto para o login com GitHub

- Status: Aceita
- Data: 2026-10-21

## Contexto

A ADR 0004 (fase 2) diz que o painel precisa de login antes que alguém possa agir
por ele, e a ADR 0006 faz do projeto a unidade de escopo. O painel em
`app.tradvia.org` continua público e só de leitura sem login.

## Decisão

- **Identidade**: um GitHub App do Tradvia usado só para o login (fluxo OAuth
  user-to-server, `state` aleatório, PKCE S256). Sem webhook, permissões mínimas. O `state`
  é de uso único, expira em 10 minutos e também fica ligado ao navegador que
  iniciou o login por um cookie `__Host-` de vida curta, então um login não pode ser forçado
  em outra pessoa.
- **Sessão**: uma linha na tabela `sessions` do Postgres. O cookie guarda 256 bits aleatórios;
  a tabela guarda só o SHA-256 deles. Cookie `__Host-tradvia_session` (`Secure`,
  `HttpOnly`, `SameSite=Lax`, `Path=/`, sem `Domain`). 8 h deslizantes, 7 dias
  absolutos. Um cookie forjado, expirado ou desconhecido é tratado como anônimo.
- **Token**: o token de usuário do GitHub é criptografado com Fernet (chave derivada com
  HKDF de `TRADVIA_SECRET_KEY`), nunca enviado ao navegador, nunca registrado em log. Isso
  adiciona a dependência `cryptography`.
- **CSRF**: requisições que mudam estado e levam um cookie de sessão precisam de `Origin`
  (ou `Referer`) igual a `TRADVIA_DOMAIN` e de `X-Tradvia-CSRF` igual a
  HMAC(chave, cookie de sessão). Os webhooks ficam de fora: eles têm a própria
  assinatura. As leituras continuam públicas.
- **Papel**: `role_of(user, project)` pergunta ao GitHub a permissão de colaborador
  com o token da pessoa, a converte com `policy.role_from_github` nos papéis de
  `policy.py` e guarda em cache por 5 minutos por sessão. A leitura da política do
  `.tradvia.yml` no painel vem com a primeira ação.
- **Caminho de retorno**: `next` só é aceito como `/runs/...`, validado antes de ser
  guardado com o `state`; o callback o lê do banco.
- **Desligado por padrão**: sem `TRADVIA_GITHUB_APP_CLIENT_ID`,
  `TRADVIA_GITHUB_APP_CLIENT_SECRET` e `TRADVIA_SECRET_KEY` as rotas respondem 404
  e `/api/auth/me` responde `{"enabled": false}`.

## Adendo: ações do painel (fase 2 da ADR 0004)

- **Política lida com o token da pessoa**: o `.tradvia.yml` é lido do branch
  padrão com o token da pessoa conectada (exige a permissão do App **Contents:
  Read-only**). Um 404 significa os padrões da ADR 0004, como no agente. Um 401/403 ou um
  arquivo inválido recusa a ação (**falha fechada**); a mensagem nomeia a permissão
  que falta. Leituras bem-sucedidas são reaproveitadas por 60 s só para decidir quais botões
  mostrar; toda ação lê o arquivo de novo.
- **Identidade**: criar um card, adicionar `agent:ready` e comentar usam o token da
  pessoa, nunca o `GITHUB_TOKEN` do servidor.
- **Registro de auditoria**: tabela `audit_log` (projeto, autor, ação, card, resultado, detalhe,
  chave de idempotência, hora). Toda tentativa é registrada, tenha dado certo
  (`ok`), sido recusada (`denied`) ou falhado (`failed`). Ele só é lido por
  `GET .../cards/{n}/audit` e `GET .../audit`, que exigem sessão e pelo menos
  o papel `read` no projeto e são enviados com `Cache-Control: no-store`; ele
  nunca é público. A rota do projeto pagina por período (`from` inclusivo, `to` exclusivo,
  `limit` até 1000, `offset`) e exporta como JSON, CSV ou JSON Lines
  (`format=json|csv|jsonl`). Todo valor de texto passa por `redact`, então nenhum formato de chave ou
  token sai do painel, e células CSV que começam com `=`, `+`, `-` ou `@`
  ganham uma aspa inicial contra injeção de fórmulas.
- **Idempotência**: o painel envia `X-Tradvia-Idempotency-Key` em toda ação. Uma
  repetição da mesma chave pela mesma pessoa em até 5 s depois de uma ação bem-sucedida (ou
  enquanto ela ainda está rodando) é ignorada e responde `{"ok": true, "duplicate": true}`.

## Consequências

- Trocar `TRADVIA_SECRET_KEY` desconecta todo mundo (e, pela ADR 0019, torna
  ilegíveis os segredos de provedores guardados).
- Cada `/api/auth/me` pode custar uma chamada ao GitHub por projeto a cada 5 minutos.
- Status: Partial. Código e testes entregues; a produção precisa que o dono registre
  o App ([docs/github-app.md](../github-app.md)) e que um mantenedor ligue os segredos.
