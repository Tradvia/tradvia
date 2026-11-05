<!-- tradvia-translation source="docs/dashboard-navigation.md" source-sha256="806509613e91c71aa6205a3984cd16c92c5535e57a6c9005307cbc1db7b1d605" -->
# Navegação do painel

O painel (`dashboard/index.html`) tem uma barra superior fixa com as seções principais.
A seção é escolhida pelo hash da URL (`#settings`, `#help`); qualquer outro valor mostra
o painel.

## Seções

| Seção | Hash | Status |
|---|---|---|
| Painel / Dashboard | `#panel` | **Shipped** |
| Conexões / Connections (#58) | — | **Planned**: aparece desativada, com o selo "em breve" |
| Configurações / Settings | `#settings` | **Partial**: papéis só para leitura; notificações estão Planned (#47) |
| Ajuda / Help | `#help` | **Shipped**: links para o README, os KPIs do painel, projetos e ADRs |

A atualização automática só roda enquanto a visão Painel está visível.

## Itens por papel

| Sessão | Painel | Conexões | Configurações | Ajuda |
|---|---|---|---|---|
| Sem sessão (somente leitura) | sim | em breve | só idioma e tema | sim |
| Conectado, qualquer papel (`read`, `write`, `admin` ou nenhum) | sim | em breve | papéis, espaço reservado para notificações, idioma e tema | sim |

O servidor continua verificando o papel em cada ação; o menu só esconde o que você não pode usar.

## Configurações

- **Papéis e permissões**: mostra o seu papel no repositório atual e se você pode
  iniciar cards e aprovar planos. Somente leitura: papéis e permissões são editados no
  `.tradvia.yml` (veja [`tradvia.example.yml`](tradvia.example.yml) e
  [`projects.md`](projects.md)).
- **Notificações**: Planned (#47), aparece como "em breve".
- **Preferências**: idioma (PT-BR/EN/ES, de `dashboard/i18n/`) e tema (escuro/claro/sistema).

## Chaves de preferência

Guardadas no `localStorage`; o painel funciona com os valores padrão quando o armazenamento
não está disponível ou está corrompido.

| Chave | Valor |
|---|---|
| `tradvia.prefs` | JSON `{"lang": "pt" \| "en", "theme": "dark" \| "light" \| "system"}`. O idioma padrão segue o navegador; o tema padrão é escuro. |
| `tradvia.autoRefresh` | `"on"` ou `"off"` |

O tema é aplicado por `<html data-theme>`; `system` segue `prefers-color-scheme`.
