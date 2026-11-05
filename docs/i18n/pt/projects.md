<!-- tradvia-translation source="docs/projects.md" source-sha256="81cf345f272fb87fffc3913cd959d5e1658782eff41538ba438eeed929c8380a" -->
# Projetos

O **projeto** é a unidade de escopo de tudo o que o Tradvia guarda (ADR 0006, fase 0).
Status: **Shipped** para armazenamento e API. Memória, skills e políticas por projeto estão
**Planned** para as próximas fases.

## Modelo

Tabela `projects`:

| Coluna | Significado |
|---|---|
| `id` | chave primária `bigserial` |
| `slug` | única, derivada do repositório (`Tradvia/tradvia` → `tradvia-tradvia`) |
| `kind` | `github_repo` (o único tipo hoje) |
| `external_id` | única, `owner/repo` |
| `created_at` | momento da criação |
| `settings` | `jsonb`, vazio por enquanto |

`deliveries`, `issues`, `comments`, `pull_requests` e `workflow_runs` têm
`project_id bigint NOT NULL REFERENCES projects (id)` e a chave primária
`(project_id, id)`: os ids do GitHub só são únicos dentro dos dados de um projeto, e um
id de entrega é registrado uma vez por projeto. Os índices em `issue_number` começam por
`project_id`.

### Migração

`schema.sql` é idempotente e roda na primeira conexão com o banco. Ele cria
`projects`, insere `Tradvia/tradvia`, adiciona `project_id`, atribui toda linha existente
com `NULL` a `Tradvia/tradvia`, define `NOT NULL` e troca chaves primárias de uma só coluna
por `(project_id, id)` apenas quando ainda não são compostas. Rodar de novo
não muda nada.

## Registrar projetos

`TRADVIA_PROJECTS` é uma lista de `owner/repo` separada por vírgulas (padrão: `TRADVIA_REPO`,
que por padrão é `Tradvia/tradvia`). Só esses repositórios são aceitos; cada um
ganha uma linha em `projects` na primeira vez em que é usado.

- **Webhook**: o projeto vem de `repository.full_name`. Um repositório que não
  está registrado recebe `202` e nada é gravado.
- **Backfill**: `tradvia backfill --project owner/repo` (padrão `TRADVIA_REPO`); o
  repositório precisa estar em `TRADVIA_PROJECTS`.
- **Rotas de proxy e do store**: o `{repo}` de `/api/github/repos/{repo}/...` e
  `/api/store/repos/{repo}/...` precisa estar registrado; caso contrário, `404`.

## Isolamento

Todo método do `Store` recebe `project_id` primeiro e toda consulta em uma tabela guardada
filtra por ele; um teste percorre `store.py` para garantir isso. Dois projetos podem ter os mesmos
ids e números de issue sem nunca ver as linhas um do outro.

## Visibilidade

Por enquanto todo projeto registrado é público. A visibilidade por papel chega com o login
(ADR 0004); `GET /api/projects` já devolve uma flag `public` para isso.

## API

- `GET /api/projects` → `[{"slug", "kind", "external_id", "public": true}]`.
- `GET /api/store/health` → `{"ok", "issues", "runs", "last_delivery", "projects":
  {"owner/repo": {"issues", "runs", "pull_requests", "last_delivery"}}}`. Os campos
  `issues`, `runs` e `last_delivery` do nível de cima estão **obsoletos** (primeiro projeto
  registrado); use `projects`.
- `GET /api/store/repos/{repo}/issues|pulls|actions/workflows/{agent|claude}.yml/runs`
  como antes, restritos ao projeto `{repo}`.

## Backlog

Status: **Shipped** (leitura). Criar, iniciar e reordenar cards no painel
(`POST /api/projects/{owner}/{repo}/cards`, `.../cards/{n}/start`, `.../backlog`)
está **Partial**: código e testes offline prontos; só funciona em produção quando
o GitHub App tiver a permissão **Contents: Read-only** e o login estiver ligado
([docs/github-app.md](github-app.md)). Um card novo entra no fim do backlog.

O backlog é guardado por projeto na tabela `backlog_items` (`project_id`,
`issue_number`, `position`, `depends_on int[]`, `note`, `updated_at`; chave primária
`(project_id, issue_number)`). `set_backlog` troca a ordem inteira em uma só
transação; toda consulta filtra por `project_id`.

- CLI: `tradvia backlog set --project owner/repo 44 45 46 ...` grava a ordem
  (posição = ordem dos argumentos; duplicados e valores não inteiros são rejeitados) e lê
  as dependências da descrição de cada issue, em linhas como `Depende do #N` ou
  `Depends on #N`. `tradvia backlog show --project owner/repo` mostra a ordem.
  `--project` tem como padrão `TRADVIA_REPO`.
- API: `GET /api/store/repos/{repo}/backlog` devolve os itens em ordem com
  `issue_number`, `position`, `title`, `state`, `labels` (da tabela `issues`),
  `depends_on`, `note` e `status`. 404 para projeto não registrado, 503 sem banco.
- Status, verificado nesta ordem: `concluído` (issue fechada), `em andamento` (label
  `agent:ready` ou um PR aberto do branch `tradvia/issue-N`), `bloqueado` (alguma
  dependência está aberta ou é desconhecida), `pronto`.
- Painel: uma coluna Backlog antes das colunas de cards, mostrada só quando a API do
  store responde.

## Painel

O cabeçalho tem um seletor de projeto alimentado por `/api/projects`. Ele mantém `?repo=` na URL
e fica escondido quando a API não está disponível.

Com login, um sino mostra quantos cards dos seus projetos esperam por você, com a lista
por projeto e notificações Web Push opcionais por usuário e projeto; a caixa de pendências
segue os papéis do `.tradvia.yml` de cada projeto. Veja
[notifications.md](notifications.md). `?card=N` abre esse card ao carregar.
