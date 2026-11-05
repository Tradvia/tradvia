<!-- tradvia-translation source="docs/durable-execution.md" source-sha256="dc35799e88f194796bf86f9e04ddda3a1768d08d58d4ed7a508b172dd6c1db94" -->
# Ejecución duradera

Estado: **Shipped** (ADR 0006, fase 1). El grafo del card guarda un checkpoint en cada paso, así que una
ejecución que muere a mitad de un card se reanuda en lugar de empezar de cero.

## Thread id

Cada card tiene un thread estable: `<project_id>:<repo>#<issue>`, por ejemplo
`1:Tradvia/tradvia#40`. El id del proyecto va primero y también se guarda en los metadatos
de cada checkpoint, así que el mismo número de issue en dos proyectos nunca comparte un
thread, y `history` y `fork` nunca devuelven ni copian datos de otro proyecto.
Sin base de datos, el id del proyecto es el id local fijo `0`.

## Reanudar

`tradvia --issue N` busca el thread del card antes de hacer nada:

- un checkpoint con una pregunta pendiente para una persona: espera la respuesta y continúa
  (el planner no se vuelve a llamar);
- un checkpoint con pasos pendientes: continúa desde él;
- ningún checkpoint (o una ejecución terminada): empieza una ejecución nueva.

## Espera duradera en CI

Con `--wait 0`, la ejecución comprueba una vez si hay respuesta y sale, dejando el card en
`WAITING_HUMAN`. El checkpoint en Postgres guarda el plan, así que una respuesta que llega
horas después reanuda el card en lugar de volver a planificarlo:

- la etiqueta `agent:ready` inicia la ejecución, que publica el plan y termina;
- un comentario de un OWNER / MEMBER / COLLABORATOR en la issue inicia una nueva ejecución que
  encuentra el checkpoint, lee la respuesta y continúa.

`DATABASE_URL` debe pasarse al job (como secreto del repositorio) para que esto funcione.
Sin ella, el checkpoint queda en memoria y se perdería entre jobs, así que la CLI
muestra un aviso de que la espera duradera está desactivada y vuelve a esperar en el
mismo job (`--wait 3600`).

Estado del lado del workflow de CI (`.github/workflows/agent.yml` pasando `DATABASE_URL`
y disparándose con comentarios): **Partial**. El lado de la CLI está entregado; el cambio en el workflow
todavía debe hacerlo un mantenedor.

## Historial y fork

```
tradvia history --issue N                       # step, node, status, date, checkpoint id
tradvia fork --issue N --checkpoint <ID>        # copy that checkpoint to a new thread
```

`fork` crea `<thread>~fork-<n>` en el mismo proyecto, empezando en el checkpoint
elegido. El thread original no cambia, y un checkpoint que no
pertenece al thread del card se rechaza. Ambos comandos leen `TRADVIA_REPO` y
`DATABASE_URL` del entorno (o de `.env`); ninguno necesita `GITHUB_TOKEN`.

## Almacenamiento

- **Con `DATABASE_URL`**: los checkpoints viven en Postgres a través de
  `langgraph-checkpoint-postgres`, que crea sus propias tablas en el primer uso.
- **Sin ella**: se usa un almacenamiento en memoria y nada se conecta a una base de datos
  (pruebas offline y `TRADVIA_OFFLINE=1`). Los checkpoints se pierden cuando el proceso termina.

### Nueva dependencia

`langgraph-checkpoint-postgres>=2` se agrega a los extras `api` y `dev`. Reutiliza
los paquetes `psycopg` y `psycopg-pool` que ya estaban y solo se importa cuando
`DATABASE_URL` está definida, así que la instalación base no la necesita.

## Límites

Un fork copia el estado del checkpoint, no las respuestas pendientes de interrupción: reanudar un fork
que está en el paso de aprobación vuelve a hacer la pregunta.
