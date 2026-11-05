<!-- tradvia-translation source="docs/adr/0005-hosting-on-oci-always-free.md" source-sha256="54e0e4cd141bd04add6a299600a3e4fbce4a65e9c070eec6c1f9b9f379737c73" -->
# ADR 0005: Alojamiento en Oracle Cloud (OCI) Always Free

- Estado: Aceptada
- Fecha: 2026-10-19

## Contexto

Hasta ahora todo corría en GitHub: Pages para el sitio y el panel, Actions
para los agentes. Eso chocó con límites que bloquean los siguientes pasos:

- El panel llama a la API de GitHub desde el navegador sin token, así que cada
  visitante tiene 60 solicitudes por hora y no se pueden mostrar proyectos privados
  (la ADR 0004 exige inicio de sesión con GitHub para ellos).
- No hay lugar para una API de larga duración, una base de datos para cards y costos, ni
  los session workers con estado y el Live Session Takeover planificados en la ADR 0003.

Se compararon los planes gratuitos en octubre de 2026. Varios populares ya no sirven:
Vercel Hobby prohíbe el uso comercial, Fly.io y Koyeb dejaron de ofrecer cómputo gratuito,
Render borra las bases de datos gratuitas después de 30 días. OCI Always Free es la única
opción con una máquina siempre encendida.

## Decisión

Ejecutar la infraestructura propia de Tradvia en una instancia OCI Always Free en la
región principal de la cuenta:

| Recurso (Always Free, oct. 2026) | Uso |
|---|---|
| E2.1.Micro, 1 OCPU / 1 GB de RAM, más 2 GB de swap (host actual) | Host Docker: proxy inverso, panel, base de datos, API pequeña |
| A1.Flex, hasta 2 OCPU / 12 GB de RAM (1.500 OCPU-h y 9.000 GB-h por mes) | Host objetivo cuando haya capacidad: deja espacio para session workers |
| Volumen de bloque, 200 GB en total | Volumen de arranque y datos |
| Transferencia saliente, 10 TB por mes | Tráfico del panel y de la API |

Organización en la instancia (`infra/oci/`):

- **Caddy**: el único servicio público, puertos 80/443, HTTPS automático para
  `app.tradvia.org`, encabezados de seguridad.
- **API**: siguiente card. Llama a GitHub desde el servidor con un token, guarda las
  respuestas en caché e implementa el inicio de sesión con GitHub (ADR 0004).
- **PostgreSQL 16**: solo en la red interna, nunca publicado en internet.
- **Panel**: servido como archivos estáticos de `dashboard/`.

Los deploys se ejecutan desde GitHub Actions por SSH con una clave solo de deploy, en un
entorno protegido `production` que necesita la aprobación de un mantenedor.

`tradvia.org` (el sitio institucional) sigue en GitHub Pages por ahora. Moverlo es
una decisión aparte.

## Reglas

1. Solo los puertos 22, 80 y 443 están abiertos: en la security list de OCI y en el
   firewall de la instancia. SSH acepta solo claves, sin contraseñas ni inicio de sesión de root.
2. Los secretos (clave de deploy, tokens de GitHub, client secret de OAuth, contraseña de la base de datos)
   viven en los secretos de entorno de GitHub o en un `.env` accesible solo para root en la
   instancia. Nunca en el repositorio.
3. Mantenerse dentro de los límites de Always Free. Cualquier cambio que cree un recurso
   de pago necesita una ADR nueva.
4. Oracle recupera las instancias Always Free que se mantienen por debajo del 20% de CPU, red y
   memoria durante 7 días. Mitigación: cargas reales (API, base de datos, workers). Si
   no alcanza, pasar el tenancy a Pay As You Go, donde la
   recuperación no aplica, manteniendo el uso dentro de los límites gratuitos.

## Consecuencias

- Tradvia obtiene un host siempre encendido sin costo, con espacio para la API y los
  primeros session workers.
- El fundador es dueño de la cuenta OCI: registro, verificación de la tarjeta, 2FA y la
  elección de la región principal (no se puede cambiar después).
- La capacidad para instancias A1 suele faltar en regiones con mucha demanda. En São Paulo
  (un dominio de disponibilidad) faltó el 2026-10-19, así que el primer host es un
  E2.1.Micro. Alcanza para Caddy, el panel y un Postgres pequeño; la
  API debe seguir siendo liviana. Pasar a A1 más adelante implica una instancia nueva y un nuevo
  deploy, ya que la familia del shape no se puede cambiar en el lugar.
- Los límites gratuitos cambian sin aviso, como hizo Oracle en junio de 2026 (A1 pasó de
  4 OCPU / 24 GB a 2 OCPU / 12 GB). La configuración sigue siendo lo bastante pequeña para caber y
  está documentada para poder moverse a otro lugar.
- El workflow del agente puede correr en el host como runner self-hosted (variable de
  repositorio `TRADVIA_AGENT_RUNS_ON`). Solo lo dispara `issues: labeled`, así que solo
  las personas con acceso de triage pueden ejecutar código allí; `@claude` y todos los demás workflows
  siguen en los runners alojados por GitHub. En el host de 1 GB está limitado por los límites de memoria
  de systemd y ejecuta un card a la vez.
