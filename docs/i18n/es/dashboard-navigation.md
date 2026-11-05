<!-- tradvia-translation source="docs/dashboard-navigation.md" source-sha256="806509613e91c71aa6205a3984cd16c92c5535e57a6c9005307cbc1db7b1d605" -->
# Navegación del panel

El panel (`dashboard/index.html`) tiene una barra superior fija con las secciones principales.
La sección se elige por el hash de la URL (`#settings`, `#help`); cualquier otro valor muestra
el panel.

## Secciones

| Sección | Hash | Estado |
|---|---|---|
| Painel / Dashboard | `#panel` | **Shipped** |
| Conexões / Connections (#58) | — | **Planned**: aparece desactivada, con la etiqueta "pronto" |
| Configurações / Settings | `#settings` | **Partial**: los roles son de solo lectura; las notificaciones están Planned (#47) |
| Ajuda / Help | `#help` | **Shipped**: enlaces al README, los KPIs del panel, proyectos y ADR |

La actualización automática solo corre mientras la vista Panel está visible.

## Elementos por rol

| Sesión | Panel | Conexiones | Configuración | Ayuda |
|---|---|---|---|---|
| Sin sesión (solo lectura) | sí | pronto | solo idioma y tema | sí |
| Con sesión, cualquier rol (`read`, `write`, `admin` o ninguno) | sí | pronto | roles, espacio reservado para notificaciones, idioma y tema | sí |

El servidor sigue verificando el rol en cada acción; el menú solo oculta lo que no puedes usar.

## Configuración

- **Roles y permisos**: muestra tu rol en el repositorio actual y si puedes iniciar
  cards y aprobar planes. Solo lectura: los roles y permisos se editan en
  `.tradvia.yml` (ver [`tradvia.example.yml`](tradvia.example.yml) y
  [`projects.md`](projects.md)).
- **Notificaciones**: Planned (#47), aparece como "pronto".
- **Preferencias**: idioma (PT-BR/EN/ES, de `dashboard/i18n/`) y tema (oscuro/claro/sistema).

## Claves de preferencia

Se guardan en `localStorage`; el panel funciona con los valores por defecto cuando el
almacenamiento no está disponible o está dañado.

| Clave | Valor |
|---|---|
| `tradvia.prefs` | JSON `{"lang": "pt" \| "en", "theme": "dark" \| "light" \| "system"}`. El idioma por defecto sigue al navegador; el tema por defecto es oscuro. |
| `tradvia.autoRefresh` | `"on"` u `"off"` |

El tema se aplica con `<html data-theme>`; `system` sigue `prefers-color-scheme`.
