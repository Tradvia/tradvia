<!-- tradvia-translation source="docs/durable-execution.md" source-sha256="dc35799e88f194796bf86f9e04ddda3a1768d08d58d4ed7a508b172dd6c1db94" -->
# Execução durável

Status: **Shipped** (ADR 0006, fase 1). O grafo do card grava um checkpoint a cada passo, então uma
execução que morre no meio de um card retoma em vez de começar do zero.

## Thread id

Cada card tem uma thread estável: `<project_id>:<repo>#<issue>`, por exemplo
`1:Tradvia/tradvia#40`. O id do projeto vem primeiro e também fica guardado nos metadados
de cada checkpoint, então o mesmo número de issue em dois projetos nunca compartilha uma
thread, e `history` e `fork` nunca devolvem nem copiam dados de outro projeto.
Sem banco de dados, o id do projeto é o id local fixo `0`.

## Retomar

`tradvia --issue N` procura a thread do card antes de fazer qualquer coisa:

- um checkpoint com uma pergunta pendente para uma pessoa: espera a resposta e continua
  (o planner não é chamado de novo);
- um checkpoint com passos pendentes: continua a partir dele;
- nenhum checkpoint (ou uma execução terminada): começa uma execução nova.

## Espera durável no CI

Com `--wait 0`, a execução verifica uma vez se há resposta e sai, deixando o card em
`WAITING_HUMAN`. O checkpoint no Postgres guarda o plano, então uma resposta que chega
horas depois retoma o card em vez de planejá-lo de novo:

- a label `agent:ready` inicia a execução, que posta o plano e termina;
- um comentário de um OWNER / MEMBER / COLLABORATOR na issue inicia uma nova execução que
  encontra o checkpoint, lê a resposta e continua.

`DATABASE_URL` precisa ser passado ao job (como segredo do repositório) para isso funcionar.
Sem ele, o checkpoint fica em memória e se perderia entre os jobs, então a CLI
mostra um aviso de que a espera durável está desligada e volta a esperar no
mesmo job (`--wait 3600`).

Status do lado do workflow de CI (`.github/workflows/agent.yml` passando `DATABASE_URL`
e disparando em comentários): **Partial**. O lado da CLI está entregue; a mudança no workflow
ainda precisa ser feita por um mantenedor.

## Histórico e fork

```
tradvia history --issue N                       # step, node, status, date, checkpoint id
tradvia fork --issue N --checkpoint <ID>        # copy that checkpoint to a new thread
```

`fork` cria `<thread>~fork-<n>` no mesmo projeto, começando no checkpoint
escolhido. A thread original não muda, e um checkpoint que não
pertence à thread do card é rejeitado. Os dois comandos leem `TRADVIA_REPO` e
`DATABASE_URL` do ambiente (ou do `.env`); nenhum precisa de `GITHUB_TOKEN`.

## Armazenamento

- **Com `DATABASE_URL`**: os checkpoints ficam no Postgres por meio do
  `langgraph-checkpoint-postgres`, que cria as próprias tabelas no primeiro uso.
- **Sem ele**: usa-se um armazenamento em memória e nada se conecta a um banco
  (testes offline e `TRADVIA_OFFLINE=1`). Os checkpoints se perdem quando o processo termina.

### Nova dependência

`langgraph-checkpoint-postgres>=2` foi adicionado aos extras `api` e `dev`. Ele reaproveita
os pacotes `psycopg` e `psycopg-pool` que já estavam lá e só é importado quando
`DATABASE_URL` está definido, então a instalação básica não precisa dele.

## Limites

Um fork copia o estado do checkpoint, não as respostas pendentes de interrupção: retomar um fork
que está no passo de aprovação faz a pergunta de novo.
