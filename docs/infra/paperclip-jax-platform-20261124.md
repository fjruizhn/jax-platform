# Paperclip · endurecimiento de JAX Platform · 2026-11-24

**Procedencia:** pendiente Paperclip de `PENDIENTES.md`, auditoría adversarial Sol sobre `76ed35ab`, 2026-11-24. Implementación: Codex. La autoridad de despliegue sigue siendo Fernando.

## Hecho

El frontend acepta enlaces externos solo si son URLs absolutas `http` o `https`; los valores con otros esquemas quedan como texto o no muestran el enlace de PR. El runner del Ejecutor recibe una lista explícita de variables usadas por `jax/ejecutor`, en lugar de heredar todo el entorno del servicio. La configuración del frontend estático genera una CSP cuyo hash sale de los bytes exactos del script `tema-inicial` del `dist/index.html` que se entrega. La política mantiene `style-src 'self' 'unsafe-inline'` porque el frontend existente usa estilos React inline para datos de facetas, barras y límites; `script-src` permanece restringido a `self` y al hash del tema.

## Por qué

Las fuentes de pipelines y el URL de un PR son datos externos. El proceso de ejecución no necesita secretos de sesión, correo, Telegram ni OAuth de Claude. El script de tema debe correr antes de React sin abrir `unsafe-inline` en CSP.

## Límites y pendiente operativo

La CSP en `ops/frontend/switch.py` cubre únicamente el modo nginx estático opcional, pero su helper puro ahora genera la política de transición con hashes de los bytes exactos de dos `index.html` (publicado viejo y bundle nuevo), y después sólo el nuevo. El sitio público vigente se sirve desde `atem-ai:/www/wwwroot/axioma-ia.io` por aaPanel, según `docs/runbooks/despliegue.md`; no hay un vhost versionado de ese sitio en este repositorio. El runbook define respaldo y restauración verificables de vhost y sitio, `nginx -t`/reload, hash dual antes de copiar el bundle, hash nuevo posterior, HTTP en `/`, `/index.html`, SPA y `/api/health`, más comprobación en navegador. Sacar `FERNET_KEY` o dependencias de JAX del runner requiere cambiar el contrato de capacidades, por lo que no forma parte de este cambio.

El 2026-11-24, Codex aplicó **solo la CSP del bundle público vigente** en aaPanel bajo la ventana abierta de Fernando; el código de esta rama aún no se había integrado ni desplegado. El vhost `/www/server/panel/vhost/nginx/axioma-ia.io.conf` pasó de SHA-256 `6fe66320ccc89c1e475ab2a55124d345ee45d0dd82c673438ee503f30c28a895` a `cb093f3cd84f59308024ccb8964dbe7d1ee598cf7716bc87cfa72ffa5b6957b4`: se añadió una política idéntica al servidor HTTPS y a `location = /index.html`, donde el encabezado `Cache-Control` impedía heredarla. El hash del `index.html` publicado siguió `1959a70b0b4bf0cbad30f85c88e91256952c54ba23698734d7f67902c5e07634`, con `script-src 'self' 'sha256-zpbirb90J62bxNkXM8W1LeH8/MFkPx1qafWq4fBXvLk='`. El respaldo privado `/home/fruiz/respaldos-sitio/axioma-csp-20261124-I39Nq8` contiene el vhost y `sitio.tar`; se probó restaurar el tar a un directorio temporal, `tar --compare` y el hash del índice. `nginx -t` y reload pasaron, sin reiniciar. Desde la URL pública `/`, `/index.html` y una ruta SPA devolvieron 200 con una sola CSP idéntica; `/api/health` y LAS MANOS siguieron vivos. Chrome cargó el login en tema claro y oscuro sin infracciones CSP. No se probó una sesión Office autenticada. Reversión: restaurar el vhost del respaldo, comprobar `nginx -t`, recargar y verificar encabezados e índice. Fuente: hashes, respaldo y pruebas directas de producción; decisión de actuar: ventana de Fernando; ejecución: Codex.

Queda pendiente publicar el código endurecido del frontend y runner mediante un par sellado. El par activo usa Platform `90626d129c7fd12f219d8acb9ef2319338f59cb3` y JAX `d62721f4438cd80a579e2ba48fcf05c04dffdc19`; integrar `master` completo en ese par arrastraría cambios ajenos. Fernando decide si se prepara un backport auditado sobre la versión activa o una actualización completa con migraciones y carga medidas.

## Lecciones y alternativas

Hashar el texto normalizado del script es incorrecto: CSP compara los bytes reales, incluidos saltos y sangrías. Se descartó cambiar de hash antes de publicar el bundle porque bloquearía el tema del `index.html` todavía servido; la política dual es sólo la transición y se reduce al hash nuevo tras verificar la copia. Se descartó `style-src 'self'` porque bloquearía los estilos inline que la UI ya necesita; se conserva el límite estricto sobre scripts. Se descartó heredar el entorno completo por su radio de exposición; la lista blanca conserva las cinco variables de base de datos y el contrato del Ejecutor.

## Rendimiento

No hay consultas ni índices nuevos. No se añadió caché ni trabajo asíncrono. La validación de URL es local y constante por elemento renderizado. Este cambio no agrega endpoints ni cambia el camino de solicitud, por lo que no requiere una prueba de carga nueva para la CSP aplicada al bundle vigente. La publicación de un bundle nuevo y del runner conserva las puertas de carga del runbook de despliegue.
