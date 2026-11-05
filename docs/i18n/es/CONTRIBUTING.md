<!-- tradvia-translation source="CONTRIBUTING.md" source-sha256="e5f2f6ba819d63dafdde636852de6387e260045a7259cb5134fec5cd2440c69d" -->
# Contribuir a Tradvia

¡Gracias por tu interés en Tradvia!

## Antes de empezar

1. **Firma el CLA.** En tu primer pull request, el bot del CLA te pedirá que
   firmes el [Contributor License Agreement](CLA.md) con un comentario. Los pull
   requests no se pueden fusionar hasta que esté firmado.
2. **Abre primero una issue** para cualquier cosa más grande que una corrección pequeña, así
   acordamos el enfoque.

## Entorno de desarrollo

Abre un entorno listo para programar en un clic con **Code → Codespaces**, o usa
el Dev Container localmente. Luego ejecuta `make test`. Los detalles, y cómo Tradvia
trabaja en sus propias issues, están en [docs/dev-environment.md](docs/dev-environment.md).

¿Buscas algo que hacer? Elige una issue con la etiqueta `good first issue`, o
propón una que el agente pueda tomar con la etiqueta `agent:ready`.

## Dónde va tu cambio

| Lo que cambias | Directorio | Licencia |
|---|---|---|
| Funcionalidades de la comunidad, correcciones, documentación | `core/` | FSL-1.1-ALv2 |
| SSO, WhatsApp Business, Teams y otros módulos enterprise | `ee/` | Comercial |

`core/` nunca debe importar de `ee/`. El CI falla si lo hace.

## Flujo de trabajo

1. Haz un fork del repositorio y crea un branch desde `main`:
   `feat/<nombre-corto>`, `fix/<nombre-corto>` o `docs/<nombre-corto>`.
2. Usa [Conventional Commits](https://www.conventionalcommits.org/):
   `feat(core): add metric retention setting`.
3. Agrega el encabezado SPDX a cada archivo fuente nuevo:
   - en `core/`: `SPDX-License-Identifier: FSL-1.1-ALv2`
   - en `ee/`: `SPDX-License-Identifier: LicenseRef-Tradvia-Commercial`
4. Ejecuta `scripts/check-license-boundary.sh` localmente.
   Traer una idea, una dependencia o código de otro proyecto sigue la
   [ADR 0023](docs/adr/0023-third-party-ideas-and-code.md): reimplementa las ideas, verifica la
   licencia exacta de una dependencia y fija su versión, guarda los pequeños fragmentos copiados en
   `core/vendor/<proyecto>/` con su `LICENSE` y una entrada en `NOTICE`, y nunca hagas un fork.
5. Abre un pull request contra `main`. `main` está protegido: los cambios solo entran
   mediante pull requests revisados con checks en verde, fusionados por squash.

## Código de conducta

Este proyecto sigue el [Código de Conducta](CODE_OF_CONDUCT.md).
