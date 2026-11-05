<!-- tradvia-translation source="docs/deploy-oci.md" source-sha256="782fe4977af58a5fde899b0e05b8edae444c874981a8fc7dfd3376083f37a0bc" -->
# Deploy do Tradvia no OCI Always Free

Decisão e regras: [ADR 0005](adr/0005-hosting-on-oci-always-free.md).
Resultado: `https://app.tradvia.org/runs/` servido por uma instância OCI sempre ligada,
com deploy pelo GitHub Actions depois que um mantenedor aprova.

Os passos marcados com **(dono)** precisam do dono da conta: envolvem login, verificação de
pagamento ou segredos, e não podem ser feitos por um agente.

## 1. Criar a conta OCI (dono)

1. Cadastre-se em <https://signup.cloud.oracle.com>. Um cartão é usado só para
   verificar a identidade.
2. **Escolha a região principal com cuidado: ela não pode ser trocada**, e a computação
   Always Free só roda nela. Para quem está no Brasil: `Brazil East (São Paulo)` ou
   `Brazil Southeast (Vinhedo)`.
3. Ative o MFA na conta.

## 2. Criar a instância (dono)

Console: **Compute > Instances > Create instance**.

| Campo | Valor |
|---|---|
| Name | `tradvia-host` |
| Image | Canonical Ubuntu 24.04 |
| Shape | `VM.Standard.E2.1.Micro` (Always Free, x86, 1 GB de RAM). Se houver disponível, `VM.Standard.A1.Flex` com até 2 OCPU / 12 GB é melhor |
| Networking | VCN nova com sub-rede pública, atribuir um endereço IPv4 público |
| SSH keys | Envie a **sua própria** chave pública (guarde a chave privada no seu computador) |
| Boot volume | 50 GB (padrão) |
| Initialization script | Cole o conteúdo de [`infra/oci/cloud-init.yaml`](../infra/oci/cloud-init.yaml) |

O A1.Flex costuma responder "Out of capacity" em regiões com um único domínio de
disponibilidade (São Paulo respondeu assim em 2026-10-19, mesmo com 1 OCPU / 6 GB). O E2.1.Micro normalmente
tem capacidade. O cloud-init funciona nos dois e adiciona 2 GB de swap.

Depois libere HTTP/HTTPS na sub-rede: **Networking > Virtual cloud networks >
(sua VCN) > Security Lists > Default** → adicione duas regras de entrada, origem
`0.0.0.0/0`, TCP, portas de destino `80` e `443`.

## 3. Apontar o domínio (dono)

No provedor de DNS de `tradvia.org`, crie um registro `A`:
`app` → o IP público da instância. Deixe o próprio `tradvia.org` onde está.

## 4. Chave de deploy e segredos (dono)

No seu computador (não na instância):

```bash
ssh-keygen -t ed25519 -f tradvia-deploy -N "" -C "github-actions deploy"
```

Adicione `tradvia-deploy.pub` à instância (`~/.ssh/authorized_keys` do usuário
`ubuntu`), usando a sua própria sessão SSH. Obtenha a chave do host para fixá-la:

```bash
ssh-keyscan -t ed25519 <public-ip>
```

No GitHub: **Settings > Environments > New environment `production`**, adicione
você mesmo como **required reviewer** e depois adicione:

| Tipo | Nome | Valor |
|---|---|---|
| Secret | `OCI_HOST` | o IP público |
| Secret | `OCI_SSH_KEY` | conteúdo de `tradvia-deploy` (chave privada) |
| Secret | `OCI_KNOWN_HOSTS` | a linha do `ssh-keyscan` |
| Secret | `POSTGRES_PASSWORD` | uma senha aleatória longa |
| Variable | `TRADVIA_DOMAIN` | `app.tradvia.org` |
| Variable | `ACME_EMAIL` | e-mail para os avisos do certificado HTTPS |

Apague a chave privada local `tradvia-deploy` depois de salvá-la como segredo.

## 5. Deploy

**Actions > deploy-oci > Run workflow**, depois aprove o deploy de `production`.
O workflow envia `infra/oci/` e `dashboard/`, sobe os
serviços e verifica `https://app.tradvia.org/healthz`.

## Fila de deploy

O workflow roda no grupo de concorrência `deploy-oci` com `cancel-in-progress: false`.
Um deploy que já está rodando nunca é interrompido por um push mais novo, e os deploys nunca
rodam lado a lado. O GitHub mantém uma execução rodando e uma pendente por grupo, e uma
execução pendente mais nova cancela a pendente mais antiga
([control workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)).

Uma execução que espera aprovação em `production` segura o grupo. Em 24 de outubro uma execução esperou
mais de uma hora, e todo push depois dela entrou na fila atrás dela sem pedir
aprovação, até alguém cancelar a execução mais antiga à mão. O painel mostra duas coisas
a quem pode aprovar:

- **Esperando demais.** Um deploy que espera aprovação há mais de
  `TRADVIA_DEPLOY_WAIT_ALERT_MINUTES` minutos (padrão `30`) ganha um botão ⏳ no
  cabeçalho. O tempo conta desde a criação da execução. O botão só lembra:
  nada é enviado para lugar nenhum.
- **Substituído.** Uma execução esperando é marcada como *substituída* quando uma execução mais nova do mesmo workflow
  também está esperando. O painel a destaca como a execução que bloqueia o deploy mais novo, e
  o botão **Cancelar e liberar a fila** a cancela. Cancelar continua sendo uma ação de uma pessoa
  no painel ([ADR 0007](adr/0007-tradvia-as-the-single-interaction-surface.md)).

Para liberar a fila de uma execução que espera demais, rejeite-a: a execução termina e o próximo
deploy pede aprovação. Aprovar a execução roda aquele deploy.

## O que roda onde

| Serviço | Exposição | Observações |
|---|---|---|
| Caddy | portas 80/443 | HTTPS automático, cabeçalhos de segurança |
| Painel | `/runs/` via Caddy | Arquivos estáticos de `dashboard/` |
| PostgreSQL 16 | só na rede interna do Docker | Armazenamento dos webhooks (fatia 2 da API) |
| API | `/api/*` via Caddy, sem porta publicada | Proxy de leitura do GitHub com cache |

## API

O serviço `api` (`core/src/tradvia/api.py`, Starlette + uvicorn, limite de 256 MB)
elimina o limite anônimo de 60 requisições/hora por IP de visitante que o painel
atinge quando chama o GitHub pelo navegador. Em `app.tradvia.org` o painel
detecta `/api/healthz` e usa `/api/github/...`; em outros lugares continua chamando o
GitHub diretamente.

- `GET /api/healthz` devolve `{"ok": true}`.
- `GET /api/github/{path}` repassa para `https://api.github.com/{path}` só para
  os repositórios de `TRADVIA_PROJECTS` (separados por vírgula; padrão `TRADVIA_REPO`,
  que por padrão é `Tradvia/tradvia`): `issues`, `issues/{n}/timeline`,
  `issues/{n}/comments`, `pulls`, `actions/runs` e as execuções dos workflows `agent.yml` e
  `claude.yml`. Todo o resto é 404. Só `state`, `per_page`,
  `page`, `labels`, `sort` e `direction` são repassados.
- Cache: em memória, por URL, TTL de 60 s (`TRADVIA_API_CACHE_TTL`), 500 entradas,
  revalidado com `ETag`. Se o GitHub falhar, uma entrada vencida é servida com
  `X-Tradvia-Stale: 1`.

Segredo opcional `TRADVIA_GITHUB_TOKEN`: um token fine-grained com acesso só de leitura
a Issues, Pull requests e Actions em `Tradvia/tradvia`. Um mantenedor
o liga ao workflow de deploy como `GITHUB_TOKEN` no `.env`. Sem ele, a
API funciona no limite anônimo do GitHub. O token nunca é devolvido nem vai para o log.

Os cards recorrentes (#133, Configurações → Cards recorrentes) são criados com esse mesmo token, porque nenhuma
pessoa está conectada quando um agendamento dispara. Para eles, o token precisa de **Issues: Read and write**; com o
token só de leitura o GitHub recusa o card, nada é criado e a tela de Configurações mostra a
recusa ao lado do agendamento.

| Funcionalidade | Status |
|---|---|
| Proxy de leitura do GitHub com cache | Shipped |
| Webhooks do GitHub guardados no Postgres | Shipped |
| Painel lendo issues, PRs e execuções do banco | Shipped |
| Linha do tempo do card a partir do banco | Planned |
| Login com GitHub | Partial: implementado, fica desligado até o GitHub App ser registrado e os segredos dele serem ligados ao deploy ([github-app.md](github-app.md)) |
| Ações a partir do painel | Planned |

## Entrar com GitHub

Opcional. A API só ativa `/api/auth/*` quando `TRADVIA_GITHUB_APP_CLIENT_ID`,
`TRADVIA_GITHUB_APP_CLIENT_SECRET` e `TRADVIA_SECRET_KEY` estão todos definidos no `.env`
(o `compose.yaml` já os passa para a API, além de `TRADVIA_DOMAIN`). Sem
eles, `GET /api/auth/me` responde `{"enabled": false}` e o painel esconde o
botão. A leitura continua pública de qualquer jeito. Registrar o App e gerar os
segredos são passos do dono: veja [github-app.md](github-app.md). Um mantenedor liga
os três valores ao workflow de deploy num pull request separado.

## Webhooks

O GitHub avisa a API de cada mudança e a API guarda no Postgres
que já roda no host (as tabelas são criadas na primeira conexão a partir de
`core/src/tradvia/schema.sql`). O painel lê issues, PRs e execuções dele
(veja "Leituras do painel" abaixo); só a linha do tempo de um card ainda vai ao GitHub.

Passo do dono, no repositório: **Settings > Webhooks > Add webhook**.

- Payload URL: `https://app.tradvia.org/api/webhooks/github`
- Content type: `application/json`
- Secret: o mesmo valor do segredo `TRADVIA_WEBHOOK_SECRET` do
  ambiente `production`
- Eventos (selecione individualmente): **Issues**, **Issue comments**,
  **Pull requests** e **Workflow runs**

Comportamento de `POST /api/webhooks/github`:

- Sem segredo configurado, ou com `X-Hub-Signature-256` ausente ou inválido: 401,
  nada é guardado. Corpos acima de 1 MB: 413.
- Outros repositórios, eventos não tratados e `ping`: 202, ignorados.
- Idempotente por `X-GitHub-Delivery`: uma entrega repetida é confirmada (202)
  e não é guardada duas vezes. Um registro só é substituído por outro com
  `updated_at` mais novo.
- Banco fora do ar: 503. Erro inesperado: 500 (a entrega é desfeita
  inteira). Nos dois casos, reenvie em **Settings > Webhooks > Recent
  Deliveries > Redeliver**; ela é processada normalmente.
- Um webhook de um repositório que não está em `TRADVIA_PROJECTS` recebe 202
  e nada é gravado. Cada entrega é guardada no projeto encontrado em
  `repository.full_name`.
- `GET /api/store/health` devolve `{"ok": true, "issues": n, "runs": n,
  "last_delivery": "...", "projects": {"owner/repo": {"issues": n, "runs": n,
  "pull_requests": n, "last_delivery": "..."}}}` (503 se o banco estiver fora do ar).
  Os campos `issues`, `runs` e `last_delivery` do nível de cima estão obsoletos (descrevem o
  primeiro projeto); use `projects`. Ele nunca devolve o texto dos comentários.
- `GET /api/projects` lista os projetos registrados. Veja `docs/projects.md`.

Carga inicial (pode ser repetida com segurança; tudo é upsert), no host:

```bash
docker compose exec api tradvia backfill                          # TRADVIA_REPO
docker compose exec api tradvia backfill --project owner/repo     # another project
```

`--project` precisa estar listado em `TRADVIA_PROJECTS`.

Depois do deploy e do backfill, guarde o backlog do projeto (ordem = ordem dos
argumentos; as dependências vêm das linhas "Depende do #N" das issues):

```bash
docker compose exec api tradvia backlog set --project owner/repo 44 45 46 ...
```

**Sem acesso SSH ao host**, rode os dois pelo GitHub: Actions → `deploy-oci` →
**Run workflow**, marque `backfill` e/ou preencha `backlog` com os números das issues na
ordem (por exemplo `44 50 45 46`). Depois do deploy e da verificação de saúde, o workflow
os executa no host para `TRADVIA_REPO` e mostra `tradvia backlog show`.
`backlog` aceita só dígitos e espaços.

Ele lê as issues com a label `agent:ready`, os comentários delas, os pull requests e as
execuções de `agent.yml` e `claude.yml`. `GITHUB_TOKEN` é opcional (ele aumenta o
limite de uso do GitHub). No host de 1 GB o banco fica limitado a 256 MB
(`shared_buffers=64MB`, `max_connections=20`) e a API usa no máximo 3
conexões.

**Depois do deploy da fatia que adiciona as leituras do painel, rode o backfill
de novo.** O esquema ganha colunas (`html_url`, `state_reason`, `name`,
`display_title`, `run_attempt`) que as linhas guardadas antes não têm; o backfill
as preenche.

## Leituras do painel

`GET /api/store/repos/{repo}/...` devolve o mesmo JSON que o GitHub para os campos
que o painel usa, a partir do Postgres, restrito ao projeto `{repo}`. Só existem repositórios
de `TRADVIA_PROJECTS` e estes caminhos (404 nos demais): `issues` (`labels`, `state`), `pulls` (`state`) e
`actions/workflows/{agent|claude}.yml/runs`. `per_page` é limitado a 100 e
os resultados vêm do mais novo para o mais antigo. O texto dos comentários nunca é exposto. Se o banco estiver
fora do ar ou ainda não tiver dados do recurso, a resposta é 503 e o painel
refaz aquela chamada por `/api/github/...`. Em `app.tradvia.org` o painel
usa o banco só quando `/api/store/health` informa `issues > 0`, e mostra
"dados: Tradvia" ou "dados: GitHub" no cabeçalho.

## Recuperação por ociosidade

A Oracle pode recuperar uma instância Always Free que fique abaixo de 20% de CPU, rede
e memória por 7 dias. A API e os workers a mantêm em uso. Se necessário, passe
o tenancy para Pay As You Go e mantenha os recursos dentro dos limites gratuitos: veja
a ADR 0005.

## Runner do agente no host da OCI (dono)

O workflow `agent` (cards com a label `agent:ready`) pode rodar no host da OCI
em vez dos runners hospedados pelo GitHub. O `@claude` (`claude.yml`) e todos os outros workflows
continuam no GitHub: eles podem ser disparados pelo comentário de qualquer pessoa, e código de
contribuidores de fora nunca pode rodar na nossa máquina. O job do agente só começa em
`issues: labeled`, que exige acesso de triage.

Status: **In progress**. O host tem 1 GB de RAM, então os cards rodam um de cada vez e
mais devagar que no GitHub; o runner é limitado pelo systemd (`MemoryHigh=600M`,
`MemoryMax=900M`) para que um card pesado não derrube o Caddy, a API ou o Postgres.
Ele vai para um host maior quando houver capacidade (ADR 0005).

1. No GitHub: **Settings → Actions → Runners → New self-hosted runner** e copie
   só o **token** que aparece na linha `./config.sh --token ...` (válido por uma hora).
   Não cole esse token em nenhum outro lugar.
2. No host:
   ```bash
   sudo bash /opt/tradvia/src/infra/oci/runner-setup.sh
   ```
   Cole o token quando for pedido (a digitação fica oculta). O script cria o
   usuário `gha-runner` (sem sudo, sem acesso ao Docker), registra o runner com a
   label `tradvia-oci` e o inicia como serviço com os limites de memória.
3. Confira que o runner aparece como **Idle** em **Settings → Actions → Runners**.
4. Troque o workflow: **Settings → Secrets and variables → Actions → Variables →
   New repository variable** `TRADVIA_AGENT_RUNS_ON` = `["self-hosted","tradvia-oci"]`.
   Para voltar aos runners hospedados pelo GitHub, apague a variável.

Comandos úteis no host:

```bash
systemctl status 'actions.runner.*'             # service state
journalctl -u 'actions.runner.*' -n 100 --no-pager
free -m; systemd-cgtop -1 --depth=2 | head -20  # memory use
```
