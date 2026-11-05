<!-- tradvia-translation source="docs/notifications.md" source-sha256="03d6337a3045ce1615ebcdfbf4fdae7643200c630ee36ea7b11ab93b83ce5263" -->
# Notificações: caixa de pendências e Web Push

Quando um card precisa de uma pessoa, o Tradvia mostra isso numa **caixa de pendências** no painel e,
se a pessoa ativou, envia uma notificação **Web Push** para o navegador ou celular dela.
Decisão: [ADR 0020](adr/0020-web-push-notifications.md).

| Funcionalidade | Status |
| --- | --- |
| Caixa de pendências (lista por projeto, contador no cabeçalho, link para o card) | Shipped |
| Web Push (VAPID, por usuário e projeto, preferências de evento) | Shipped |
| Slack | Planned |
| WhatsApp | Planned |
| E-mail | Planned |

As duas dependem do login com GitHub ([ADR 0008](adr/0008-sessions-csrf-and-project-roles.md))
e das ações do painel ([ADR 0007](adr/0007-tradvia-as-the-single-interaction-surface.md)).
Os testes rodam offline com um enviador simulado; a entrega a um serviço de push real precisa das chaves abaixo.

## O que conta como pendência

Um card está pendente para alguém quando o próximo passo é uma decisão humana e o papel
dessa pessoa (lido do `.tradvia.yml`, [ADR 0004](adr/0004-access-follows-project-configurable-interactions.md))
é suficiente para respondê-la:

| Evento (`kind`) | O que aconteceu | Interação que responde (papel padrão) |
| --- | --- | --- |
| `plan_ready` | O agente postou um plano e espera "Aprovado", "Rejeitado" ou "Ajuste" | `approve_plan` (write) |
| `agent_question` | O agente fez uma pergunta (`❓`) e espera | `approve_plan` (write) |
| `adjust_limit` | O limite de ajustes foi atingido; o card precisa de uma pessoa | `approve_plan` (write) |
| `human_request` | Um agente pediu que uma pessoa agisse (`request_human`), com o link da sessão ao vivo quando houver. Também vai para o Discord quando há um canal configurado | `live_takeover` (maintain) |
| `pr_opened` | O agente abriu um pull request que continua aberto | `merge_pr` (maintain) |
| `budget_limit` | O limite de orçamento parou o card | `budget_control` (admin) |

Uma pessoa sem papel no projeto, ou com papel abaixo da interação, não vê nada
daquele tipo e não recebe push dele. Um card sai da caixa quando alguém responde
(um comentário depois do agente, ou o PR é fechado) ou quando a issue é fechada.

## A caixa de pendências

Com login, o cabeçalho mostra um sino com o número de cards esperando por você em todos os
projetos. Clique para vê-los agrupados por projeto; cada linha leva ao card
(`?repo=owner/repo&card=N`), que abre com os controles de responder, aprovar ou fazer merge.

API: `GET /api/projects/{owner}/{repo}/pending` devolve `{count, items}` para a pessoa
conectada; `/api/auth/me` traz `pending_count` por projeto.

## Ativar o Web Push

1. **Gere as chaves VAPID uma vez** (duas strings base64url):

   ```bash
   npx web-push generate-vapid-keys
   ```

   Sem Node: `pip install py-vapid`, depois `vapid --gen` e
   `vapid --applicationServerKey` (mostra a chave pública; a privada fica em
   `private_key.pem`, cujo caminho o enviador também aceita).

2. **Defina as variáveis** no `.env` do servidor (`core/.env.example` lista todas):

   ```
   TRADVIA_VAPID_PUBLIC_KEY=...
   TRADVIA_VAPID_PRIVATE_KEY=...
   TRADVIA_VAPID_SUBJECT=mailto:you@example.org   # opcional; padrão https://$TRADVIA_DOMAIN
   ```

   A chave privada fica **apenas** no `.env` do servidor: nunca é enviada ao navegador,
   escrita em log ou devolvida pela API. O push fica desligado se faltar qualquer uma das chaves, e o painel
   então esconde os controles de notificação. Reinicie a API depois de mudá-las.

3. **Ative no painel**: entre, abra o sino, clique em **Ativar notificações** e
   aceite o pedido do navegador. O painel registra o `sw.js` (service worker), assina com
   a chave pública e guarda a assinatura para você e o projeto atual. Faça isso em cada
   projeto (e em cada navegador ou celular) do qual quer ser avisado. Exige HTTPS e um
   navegador com suporte a Push; no iOS, adicione o painel à Tela de Início antes.

4. **Escolha os eventos** nas caixas de seleção abaixo do botão; elas são guardadas por usuário e
   projeto. Por padrão todos os eventos estão ligados. **Desativar notificações** remove a assinatura.

Um push é enviado quando um webhook do GitHub registra um evento que leva um card a um dos
tipos acima, para os assinantes daquele projeto cujo papel pode respondê-lo e que mantiveram aquele
evento ligado. Webhooks reenviados não notificam duas vezes. Assinaturas que o serviço de push informa como
encerradas (HTTP 404 ou 410) são apagadas. Uma falha de push nunca muda a resposta do webhook.
O envio usa o papel guardado do assinante (da sessão ativa dele), então quem teve a sessão
expirada não recebe push até entrar de novo.

## O que a notificação contém

Só o **projeto**, o **número do card** e o **tipo de evento**, por exemplo
"tradvia-tradvia · #47 · plano pronto para aprovar". A mensagem nunca leva título de card,
comentário, texto de plano, branch ou qualquer outro conteúdo do GitHub: o service worker monta o
texto a partir desses três valores e de rótulos fixos (português, inglês ou espanhol, conforme o idioma do navegador).
O serviço de push (Google, Mozilla, Apple) vê apenas o conteúdo criptografado e o endpoint.
Clicar abre o card no painel, onde valem o login e as verificações de papel de sempre.

O servidor só envia para endpoints `https` em nomes de host públicos, já que o endpoint
da assinatura é um destino de requisição.

## Alertas proativos

Status: **In progress** (vira Shipped depois do merge).

Enquanto o servidor roda, uma verificação a cada `TRADVIA_ALERT_INTERVAL_S` segundos (padrão `300`)
avisa por Discord e Web Push sobre três coisas, uma vez por ocorrência, com o link para o
card (ou para a execução, quando não há card):

- **Fila de deploy parada.** Um deploy esperando aprovação há mais de
  `TRADVIA_DEPLOY_WAIT_ALERT_MINUTES` (padrão `30`). Execuções substituídas não alertam. Exige
  `GITHUB_TOKEN` e o login configurados.
- **Custo fora do normal.** Um card cujo custo somado é mais de `TRADVIA_COST_ALERT_FACTOR`
  (padrão `3`) vezes o custo mediano por card do projeto. Exige pelo menos 5 cards com custo
  e ignora uma mediana 0.
- **Execução sem atualização.** Uma execução ainda em andamento ou na fila sem atualização há mais de
  `TRADVIA_RUN_STALE_MINUTES` (padrão `30`).

As verificações só rodam quando o Discord (`DISCORD_BOT_TOKEN` e `DISCORD_CHANNEL_ID`) ou o Web Push
está configurado. A mensagem do Discord tem o tipo, o projeto e o link; o push tem só
projeto, número do card e tipo (`deploy_stuck`, `cost_outlier`, `run_dead`), pode ser silenciado por
tipo nas preferências e vai para quem pode respondê-lo pelo papel. Um alerta só é anunciado de novo
depois que a condição deixou de valer e voltou. Os alertas anunciados ficam em memória:
uma reinicialização pode reenviar os que ainda estão ativos. Slack e webhooks de saída estão fora do escopo.
