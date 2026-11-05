<!-- tradvia-translation source="docs/adr/0001-open-core-licensing.md" source-sha256="5ff62055259d112e4b1ecae960e7cf28ff0943626f22101aab164a743a430343" -->
# ADR 0001: Organización open-core con core FSL y EE comercial

- Estado: Aceptada
- Fecha: 2026-10-17

## Contexto

Tradvia debe poder autoalojarse e inspeccionarse libremente, para formar una
comunidad, y al mismo tiempo impedir que proveedores de nube y competidores lo vendan como
servicio gestionado sin contribuir. El modelo de negocio necesita espacio para funcionalidades
enterprise de pago (SSO, WhatsApp Business API, Microsoft Teams).

## Decisión

1. Un único repositorio (monorepo) con dos directorios de primer nivel:
   - `core/` bajo **FSL-1.1-ALv2**: cualquier uso excepto un Competing Use; cada
     release pasa a Apache 2.0 dos años después de publicarse.
   - `ee/` bajo una **licencia comercial**: código visible, el uso en producción
     requiere suscripción; las funcionalidades se habilitan con una clave de licencia.
2. `core/` nunca depende de `ee/`. `ee/` se conecta a puntos de extensión expuestos
   por `core/`. Lo garantiza el CI con `scripts/check-license-boundary.sh`.
3. Toda contribución requiere el CLA de `CLA.md`, para que la Empresa pueda licenciar
   las contribuciones bajo ambas licencias.

## Alternativas consideradas

- **Elastic License 2.0 para el core**: protección similar, pero nunca se convierte
  en una licencia open source, lo que cuesta más confianza de la comunidad.
- **AGPLv3 para el core**: open source aprobada por la OSI, pero no impide que un
  competidor aloje el software si publica sus cambios.
- **Repositorio privado separado para el EE**: más reserva, pero es más difícil mantener
  las versiones sincronizadas. Se puede revisar si el código del EE debe volverse confidencial.

## Consecuencias

- El proyecto es "fair source" / source-available, **no** open source según la OSI.
  La comunicación no debe llamarlo "open source".
- Los cambios en los archivos de licencia requieren la revisión de un admin (ver `CODEOWNERS`).
