<!-- tradvia-translation source="docs/adr/0005-hosting-on-oci-always-free.md" source-sha256="54e0e4cd141bd04add6a299600a3e4fbce4a65e9c070eec6c1f9b9f379737c73" -->
# ADR 0005: Hospedagem no Oracle Cloud (OCI) Always Free

- Status: Aceita
- Data: 2026-10-19

## Contexto

Até agora tudo rodava no GitHub: Pages para o site e o painel, Actions
para os agentes. Isso esbarrou em limites que bloqueiam os próximos passos:

- O painel chama a API do GitHub pelo navegador sem token, então cada
  visitante tem 60 requisições por hora e projetos privados não podem ser mostrados
  (a ADR 0004 exige login com GitHub para eles).
- Não há lugar para uma API de longa duração, um banco para cards e custos, nem
  os session workers com estado e o Live Session Takeover planejados na ADR 0003.

Os planos gratuitos foram comparados em outubro de 2026. Vários populares não servem mais:
o Vercel Hobby proíbe uso comercial, Fly.io e Koyeb deixaram de oferecer computação gratuita,
o Render apaga bancos gratuitos depois de 30 dias. O OCI Always Free é a única
opção com uma máquina sempre ligada.

## Decisão

Rodar a infraestrutura do próprio Tradvia numa instância OCI Always Free na
região principal da conta:

| Recurso (Always Free, out/2026) | Uso |
|---|---|
| E2.1.Micro, 1 OCPU / 1 GB de RAM, mais 2 GB de swap (host atual) | Host Docker: proxy reverso, painel, banco, API pequena |
| A1.Flex, até 2 OCPU / 12 GB de RAM (1.500 OCPU-h e 9.000 GB-h por mês) | Host alvo quando houver capacidade: abre espaço para session workers |
| Volume de bloco, 200 GB no total | Volume de boot e dados |
| Transferência de saída, 10 TB por mês | Tráfego do painel e da API |

Organização na instância (`infra/oci/`):

- **Caddy**: o único serviço público, portas 80/443, HTTPS automático para
  `app.tradvia.org`, cabeçalhos de segurança.
- **API**: próximo card. Ela chama o GitHub pelo servidor com um token, guarda as
  respostas em cache e implementa o login com GitHub (ADR 0004).
- **PostgreSQL 16**: só na rede interna, nunca publicado na internet.
- **Painel**: servido como arquivos estáticos de `dashboard/`.

Os deploys rodam a partir do GitHub Actions por SSH com uma chave só de deploy, num
ambiente protegido `production` que precisa da aprovação de um mantenedor.

`tradvia.org` (o site institucional) continua no GitHub Pages por enquanto. Movê-lo é
uma decisão separada.

## Regras

1. Só as portas 22, 80 e 443 ficam abertas: na security list da OCI e no
   firewall da instância. O SSH aceita só chaves, sem senhas e sem login de root.
2. Os segredos (chave de deploy, tokens do GitHub, client secret do OAuth, senha do banco)
   ficam nos segredos de ambiente do GitHub ou num `.env` acessível só ao root na
   instância. Nunca no repositório.
3. Ficar dentro dos limites do Always Free. Qualquer mudança que criaria um recurso
   pago precisa de uma ADR nova.
4. A Oracle recupera instâncias Always Free que ficam abaixo de 20% de CPU, rede e
   memória por 7 dias. Mitigação: cargas reais (API, banco, workers). Se
   isso não bastar, passar o tenancy para Pay As You Go, onde a
   recuperação não se aplica, mantendo o uso dentro dos limites gratuitos.

## Consequências

- O Tradvia ganha um host sempre ligado sem custo, com espaço para a API e os
  primeiros session workers.
- O fundador é dono da conta OCI: cadastro, verificação do cartão, 2FA e a
  escolha da região principal (ela não pode ser trocada depois).
- A capacidade para instâncias A1 costuma faltar em regiões movimentadas. Em São Paulo
  (um domínio de disponibilidade) ela faltou em 2026-10-19, então o primeiro host é um
  E2.1.Micro. Ele basta para o Caddy, o painel e um Postgres pequeno; a
  API precisa continuar leve. Passar para A1 depois significa uma instância nova e um novo
  deploy, já que a família do shape não pode ser trocada no lugar.
- Os limites gratuitos mudam sem aviso, como a Oracle fez em junho de 2026 (o A1 passou de
  4 OCPU / 24 GB para 2 OCPU / 12 GB). A configuração continua pequena o bastante para caber e
  está documentada para poder ir para outro lugar.
- O workflow do agente pode rodar no host como runner self-hosted (variável de
  repositório `TRADVIA_AGENT_RUNS_ON`). Só `issues: labeled` o dispara, então só
  pessoas com acesso de triage podem rodar código ali; o `@claude` e todos os outros workflows
  continuam nos runners hospedados pelo GitHub. No host de 1 GB ele é limitado pelos limites de memória
  do systemd e roda um card de cada vez.
