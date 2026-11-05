<!-- tradvia-translation source="docs/adr/0004-access-follows-project-configurable-interactions.md" source-sha256="c62e7a3e3afb6bd5bc984bf50e4220c9697a8460d54264305ae5bf30c7aa1b4e" -->
# ADR 0004: O acesso segue o projeto; as interações são configuráveis

- Status: Aceita
- Data: 2026-10-19

## Contexto

O primeiro painel de execuções entrou no ar em `tradvia.org/runs`. Ele lê dados públicos
do GitHub sem login. Isso levantou a pergunta de quem deveria ver o
painel e quem deveria poder agir pelo Tradvia: iniciar um card,
aprovar um plano, chamar um assistente ou, mais adiante, assumir uma sessão ao vivo.

Os dados por trás do painel vêm do próprio projeto (issues, pull
requests, execuções de workflow). Escondê-los no Tradvia quando o projeto é público
não protege nada. Mostrá-los no Tradvia quando o projeto é privado
os vazaria.

## Decisão

### 1. A visualização segue a visibilidade do projeto

| Projeto | Quem pode ver cards, execuções e KPIs |
|---|---|
| Público | Qualquer pessoa, só leitura, sem login |
| Privado | Só pessoas conectadas com GitHub que tenham pelo menos `read` no repositório |

O Tradvia nunca mostra mais do que o sistema de origem mostra. Um projeto pode
**restringir** ainda mais a visualização (por exemplo, exigir `read` mesmo num repositório
público), mas a configuração nunca pode **ampliá-la**.

### 2. As interações têm um papel mínimo configurável

Tudo o que muda estado é uma *interação*. Cada uma tem um papel mínimo
do GitHub, configurado por projeto. Primeiro no `.tradvia.yml` na raiz do repositório; desde
a ADR 0018, no Tradvia (Configurações, Papéis e risco do projeto), e a seção do arquivo é ignorada.
Os papéis seguem a ordem do GitHub: `read < triage < write < maintain < admin`.
A identidade é sempre a conta da pessoa no GitHub. O Tradvia não guarda usuários nem
senhas próprios.

| Interação | O que permite | Padrão | Status |
|---|---|---|---|
| `start_card` | Iniciar um card (hoje, a label `agent:ready`) | `triage` | Partial (o GitHub exige `triage` para labels) |
| `approve_plan` | Responder a um plano: aprovar, rejeitar ou ajustar | `write` | Partial (fixo no código hoje) |
| `mention_assistant` | Chamar o `@claude` em issues e pull requests | `write` | Partial (aplicado pela action) |
| `live_takeover` | Assumir uma sessão de navegador ou terminal congelada | `maintain` | Planned |
| `budget_control` | Botão de parada e limites de orçamento | `admin` | Planned |

Exemplo:

```yaml
# .tradvia.yml
access:
  view: inherit            # inherit | read | triage | write | maintain | admin
interactions:
  start_card: triage
  approve_plan: write
  mention_assistant: write
  live_takeover: maintain
  budget_control: admin
budget:                    # per-card execution limits (ADR 0006)
  max_minutes: 30
  max_steps: 40
  max_usd: 5.0
```

A seção `interactions` acima, e a seção `risk` da ADR 0006, foram substituídas do mesmo
jeito pela ADR 0018: elas são a configuração do projeto no Tradvia, alterada só por um admin.

O `.tradvia.yml` também tinha uma seção `budget` (orçamentos de execução por card, ADR 0006).
Substituída pela ADR 0018: os orçamentos agora são uma configuração do projeto no Tradvia (Configurações, Orçamento
do projeto), alterada por quem tem `budget_control`; uma seção `budget` no arquivo é ignorada.

### 3. Invariantes de segurança

1. Nenhuma interação pode ser configurada abaixo de `read`: usuários anônimos nunca agem.
2. Bots nunca contam como aprovadores, qualquer que seja o papel deles.
3. Agentes nunca podem editar o `.tradvia.yml` (ele entra nos caminhos protegidos ao lado de
   `.github/`, das licenças e do `CODEOWNERS`). Ele só muda por um pull request
   revisado.
4. Chaves desconhecidas ou papéis inválidos fazem a política falhar fechada: o Tradvia recusa
   a interação e explica o porquê, em vez de cair num acesso aberto.
5. A política é lida do branch padrão, nunca do branch de um pull request.

## Fases

1. **Política no core (próximo card):** modelo `Policy` e carregador com padrões e
   validação; `approve_plan` e `start_card` aplicados pela API de permissão de
   colaboradores do GitHub (`role_name`); `.tradvia.yml` adicionado aos
   caminhos protegidos; um arquivo de exemplo em `docs/`.
2. **Ligação com o workflow (mantenedor):** passar ao agente quem aplicou a label, para que
   `start_card` acima de `triage` possa ser aplicado; alinhar o `@claude` com
   `mention_assistant` quando ele estiver acima de `write`.
3. **Painel:** mostrar a política efetiva ("quem pode fazer o quê") nos projetos
   públicos; login com GitHub para projetos privados e para as ações interativas
   do painel, quando existirem.

## Consequências

- `tradvia.org/runs` continua público porque `Tradvia/tradvia` é público.
- Projetos privados de clientes precisam de login com GitHub antes que qualquer painel seja
  servido. Isso é um requisito obrigatório da edição Cloud.
- Os admins devem adicionar `/.tradvia.yml @Tradvia/admins` ao `CODEOWNERS` quando o
  arquivo existir, para que mudanças de política precisem da revisão de um admin.
