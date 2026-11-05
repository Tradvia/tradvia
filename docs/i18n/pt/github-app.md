<!-- tradvia-translation source="docs/github-app.md" source-sha256="54395dc5d9cabfad568c67f9718861b9e9b3f3652076f4cbdaa725001129103b" -->
# Entrar com GitHub: registrar o GitHub App

Status: **Partial.** As rotas da API, as sessões, as verificações de CSRF e o botão do painel
estão implementados e testados offline. O login só funciona em produção depois que o
dono conclui os passos abaixo e um mantenedor liga os segredos ao workflow de
deploy. As ações do painel (criar, iniciar e reordenar cards, aprovar
o plano) estão **Partial**: implementadas e testadas offline, e só funcionam em
produção com o login ligado e as permissões abaixo.

Revisar e fazer merge de PRs, aprovar deploys e a operação pós-deploy
(ADR 0007, #56) estão **Partial**: testados offline, e funcionam em produção
quando o App tiver as permissões abaixo e as instalações as aceitarem.

O GitHub App do Tradvia é principalmente um **provedor de identidade**: ele prova quem a
pessoa é e, com o token dela, qual papel ela tem em cada projeto. Ele
não tem webhook. As ações do painel rodam no GitHub com o **próprio token da pessoa**, então
o histórico do GitHub mostra quem agiu; o `GITHUB_TOKEN` do servidor nunca é usado
para elas. Desenho: ADR 0008.

## Passos do dono

1. GitHub: **Settings > Developer settings > GitHub Apps > New GitHub App**
   (na organização Tradvia, se o App deve pertencer a ela).
2. Preencha:
   - **GitHub App name**: `Tradvia`
   - **Homepage URL**: `https://tradvia.org`
   - **Callback URL**: `https://app.tradvia.org/api/auth/callback`
   - **Request user authorization (OAuth) during installation**: desligado
   - **Expire user authorization tokens**: como preferir (a sessão dura no máximo
     7 dias; o Tradvia ainda não usa refresh tokens)
   - **Webhook > Active**: **desligado** (sem webhook)
3. Permissões (de repositório), o mínimo:
   - **Issues**: Read and write. Criar cards, adicionar `agent:ready`, comentar.
   - **Pull requests**: Read and write. Ler o PR, os arquivos dele e os comentários
     de revisão; `PUT /pulls/{n}/update-branch`.
   - **Contents**: Read and write. Ler o `.tradvia.yml` do branch padrão
     com o token da pessoa, que define o papel mínimo de cada ação do painel
     (ADR 0004); sem isso, toda ação do painel é recusada (falha fechada) com uma
     mensagem que nomeia essa permissão. Escrita: `PUT /pulls/{n}/merge` (squash) e
     `DELETE /git/refs/heads/{branch}` (apagar o branch depois do merge).
   - **Actions**: Read and write. `GET /actions/runs` (deploys esperando),
     `POST /actions/runs/{id}/cancel` (deploys substituídos),
     `POST /actions/runs/{id}/approve` (workflows de um PR que o GitHub segura, veja a
     última seção desta página) e
     `POST /actions/workflows/deploy-oci.yml/dispatches` (operação pós-deploy).
   - **Deployments**: Read and write. `GET` e
     `POST /actions/runs/{id}/pending_deployments` (aprovar ou rejeitar; a
     pessoa também precisa ser revisora obrigatória do ambiente).
   - **Secrets**: Read and write. Conexões do projeto (ADR 0019): o Tradvia lê
     a chave pública do repositório e grava ou apaga os segredos do Actions `ANTHROPIC_API_KEY`,
     `GEMINI_API_KEY`, `OPENAI_API_KEY` e `CLAUDE_CODE_OAUTH_TOKEN`
     quando um admin salva ou remove um segredo de provedor. Sem isso, o
     segredo continua salvo no Tradvia e a tela diz que não foi possível gravá-lo
     no GitHub Actions.
   - **Checks**: Read-only. `GET /commits/{sha}/check-runs`: os checks `core-tests`,
     `license-boundary` e `cla` na tela do PR e antes de um merge.
   - **Metadata**: Read-only (obrigatória)

   Nada mais, e nenhuma permissão de conta. **Instalações existentes precisam aceitar
   as novas permissões:** mudá-las na página do App não vale até que
   o dono (e cada organização que instalou) aprove o pedido nas
   configurações da instalação. Até lá, os botões afetados respondem com uma
   mensagem que nomeia a permissão que falta. Uma sessão aberta antes da atualização mantém
   o token antigo, então a pessoa entra de novo depois que o dono aceitar.
4. **Where can this GitHub App be installed?** Any account, para que colaboradores de
   outros projetos registrados possam entrar. Crie o App.
5. Na página do App copie o **Client ID** e clique em **Generate a new client
   secret**; copie-o uma vez.
6. Gere a chave que criptografa os tokens das pessoas no banco:

   ```
   python -c 'import secrets;print(secrets.token_urlsafe(32))'
   ```

7. Passe os três valores para um mantenedor, que os adiciona ao ambiente `production`
   e os liga ao workflow de deploy (pull request separado):

   | Variável do `.env` | Valor |
   |---|---|
   | `TRADVIA_GITHUB_APP_CLIENT_ID` | Client ID |
   | `TRADVIA_GITHUB_APP_CLIENT_SECRET` | client secret |
   | `TRADVIA_SECRET_KEY` | a chave do passo 6 |

   Mantenha-os fora do repositório, do chat e dos logs. Trocar `TRADVIA_SECRET_KEY`
   desconecta todo mundo (os tokens guardados não podem mais ser decifrados) e torna
   ilegíveis os segredos de provedores salvos na tela Conexões: defina-os de novo.
8. Instale o App em cada projeto registrado (`TRADVIA_PROJECTS`) para que as
   consultas de permissão funcionem.

## O que a API faz

| Rota | Comportamento |
|---|---|
| `GET /api/auth/login?next=/runs/?repo=owner/repo` | Guarda um `state` aleatório, um verificador PKCE e o caminho de retorno validado, e redireciona para o GitHub. Só `/runs/...` é aceito como `next`. |
| `GET /api/auth/callback` | Verifica o `state` de uso único (e o cookie do navegador que iniciou o login), troca o código com o verificador PKCE, cria a sessão e redireciona para o caminho guardado. |
| `POST /api/auth/logout` | Apaga a sessão e limpa o cookie. |
| `GET /api/auth/me` | `login`, `avatar_url`, o valor CSRF, o papel em cada projeto e `can_start` / `can_approve` (o que o painel pode mostrar como botões). Nunca o token do GitHub. |
| `POST /api/projects/{owner}/{repo}/cards` | Cria a issue (`title`, `description`), guarda e acrescenta ao fim do backlog. Exige `start_card`. |
| `POST /api/projects/{owner}/{repo}/cards/{n}/start` | Adiciona `agent:ready`. Exige `start_card`. |
| `POST /api/projects/{owner}/{repo}/cards/{n}/reply` | `decision` = `approve` \| `reject` \| `adjust` (+ `text`) comenta "Aprovado", "Rejeitado" ou "Ajuste: …". Exige `approve_plan`; 409 a menos que o último comentário do agente seja o plano. |
| `POST /api/projects/{owner}/{repo}/backlog` | `{"issue": n, "direction": "up"\|"down"}` ou `{"order": [...]}`. Exige `start_card`. |
| `GET /api/projects/{owner}/{repo}/cards/{n}/audit` | Entradas de auditoria do card e, para quem pode aprovar, o plano completo. Exige pelo menos o papel `read`; `Cache-Control: no-store`. |
| `GET /api/projects/{owner}/{repo}/pulls/{n}` | O PR: título, descrição, resumo e custo do executor, arquivos com +/−, os checks obrigatórios, comentários de revisão e quais botões a pessoa pode usar. Exige `read`. |
| `GET /api/projects/{owner}/{repo}/pulls/{n}/files/{path}/diff` | O diff de texto de um arquivo, sob demanda. Exige `read`. |
| `POST /api/projects/{owner}/{repo}/pulls/{n}/merge` | Squash merge com o título Conventional Commit do PR, depois apaga o branch. O corpo `{"ignore_cla": true}` ignora só um check `cla` com falha (registrado na auditoria); qualquer outro check obrigatório com falha recusa o merge. Exige `merge_pr`. |
| `POST /api/projects/{owner}/{repo}/pulls/{n}/update-branch` | Atualiza o branch do PR com o main. Exige `update_branch`. |
| `POST /api/projects/{owner}/{repo}/pulls/{n}/approve-workflows` | Aprova as execuções que o GitHub segura para aprovação no commit mais recente do PR. Corpo `{"sha": "<head>"}`; 409 se o PR tiver commits novos ou nada estiver esperando. Exige `merge_pr`. |
| `GET /api/projects/{owner}/{repo}/deployments/pending` | Deploys esperando aprovação (os mais antigos da mesma fila ficam como `replaced`) e a ordem do backlog guardada no Tradvia. Exige `read`. |
| `POST /api/projects/{owner}/{repo}/deployments/{run_id}/approve` e `/reject` | Revisa o deploy pendente com o token da pessoa; a pessoa precisa ser revisora do ambiente. Exige `approve_deploy`. |
| `POST /api/projects/{owner}/{repo}/deployments/{run_id}/cancel` | Cancela um deploy substituído (qualquer outra execução é recusada). Exige `approve_deploy`. |
| `POST /api/projects/{owner}/{repo}/operations/post-deploy` | Dispara o `deploy-oci` com `{"backfill": bool, "backlog": "44 50 45"}`; o painel preenche o backlog com a ordem guardada. Exige `approve_deploy`. |

As rotas de ação respondem 401 sem sessão, 403 para CSRF ausente ou um
papel insuficiente (a mensagem nomeia o papel exigido), 404 para um projeto que não está em
`TRADVIA_PROJECTS` ou um card que não é do projeto, e 409 para um card no
estado errado. O painel envia um `X-Tradvia-Idempotency-Key` aleatório; a mesma chave
de novo em até 5 segundos é ignorada.

Toda ação de pull request e de deploy é auditada (quem, o quê, resultado; `ignore_cla`
aparece no detalhe) e roda com o token da pessoa, nunca com o do servidor.
Quando o GitHub recusa uma chamada porque falta uma permissão ao **App** (`Resource not
accessible by integration`), a resposta a nomeia, por exemplo *The GitHub App needs
Actions: Read and write*; Contents para merge e apagar branch, Pull requests para
update-branch, Deployments para aprovar e rejeitar. Uma recusa para a pessoa
diz *You are not a reviewer of the production environment.* Outras mensagens claras:
sem papel no projeto, *Merge refused: the required check(s) failed*, *Merge refused: the required check(s) wait for their workflows to be approved* e *The branch is behind main*.

As novas interações do `.tradvia.yml` são `merge_pr`, `update_branch` e
`approve_deploy` (também aprovar, rejeitar, cancelar e a operação pós-deploy);
todas têm `maintain` como padrão. Veja `docs/tradvia.example.yml`.

- Cookie `__Host-tradvia_session`: `Secure`, `HttpOnly`, `SameSite=Lax`,
  `Path=/`, sem `Domain`. Válido por 8 h desde o último uso, no máximo 7 dias desde o login.
- Todo `POST`/`PUT`/`PATCH`/`DELETE` com cookie de sessão exige o cabeçalho
  `X-Tradvia-CSRF` (valor de `/api/auth/me`) e um `Origin` (ou `Referer`)
  igual a `https://$TRADVIA_DOMAIN`. Os webhooks mantêm a própria assinatura.
- O token da pessoa é guardado criptografado (Fernet, chave derivada de
  `TRADVIA_SECRET_KEY`), nunca é enviado ao navegador e nunca vai para o log.
- Os papéis vêm de `GET /repos/{owner}/{repo}/collaborators/{login}/permission`
  com o token da pessoa. Uma resposta do GitHub fica em cache por 5 minutos por sessão; uma
  verificação que falhou não fica em cache, e a próxima requisição pergunta de novo. Sem papel, a resposta diz
  o porquê, nas rotas de ação e de leitura e por projeto em `GET /api/auth/me`
  (`role_reason` e `notice`):
  - O token de login foi revogado ou expirou (401): *Your GitHub sign-in is no longer valid*. Entre de novo.
  - O GitHub recusou a verificação (403): o App precisa de `Metadata: Read-only`. Um dono aceita a atualização na instalação, e depois a pessoa entra de novo.
  - Limite de uso do GitHub (403 ou 429): espere alguns minutos.
  - O GitHub não respondeu, ou respondeu de um jeito inesperado: tente de novo.
  - O GitHub diz que a pessoa não é colaboradora (404, ou `permission: none`): a resposta direta, *You have no access* ou *You need at least the 'read' role*.

## Aprovar os workflows que esperam num pull request

O GitHub segura as execuções de workflow de alguns pull requests até que alguém com acesso
de escrita as aprove. Isso acontece com execuções de forks e, como observado no PR #95
(issue #97), com execuções dos próprios pull requests do agente. Enquanto esperam, os
checks obrigatórios aparecem como *awaiting approval* em vez de falha, o merge é recusado
até elas rodarem, e a tela do PR mostra **Aprovar workflows** para quem pode
fazer merge (`merge_pr`, `maintain` por padrão).

O botão chama `POST /actions/runs/{id}/approve` para cada execução que o GitHub segura
no commit mais recente do PR, com o próprio token da pessoa, e grava uma entrada de auditoria
`approve_workflows`. O painel envia o commit que mostrou: se o PR
recebeu um commit novo desde então, nada é aprovado e a resposta é 409. Aprovar
inicia os workflows daquele commit com os segredos do repositório, então aprove só
código que você leu.

Se o GitHub pede ou não essa aprovação é decidido pelas configurações de Actions do
repositório (Settings → Actions → General, *Fork pull request workflows from
outside collaborators*). O Tradvia não conseguiu ler essas configurações a partir do
ambiente em que isto foi construído, então o dono do repositório deve conferi-las
lá.
