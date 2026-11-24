# Paperclip · endurecimiento de JAX Platform · 2026-11-24

**Procedencia:** pendiente Paperclip de `PENDIENTES.md`, auditoría adversarial Sol sobre `1549f799`, 2026-11-24. Implementación: Codex. La autoridad de despliegue sigue siendo Fernando.

## Hecho

El frontend acepta enlaces externos solo si son URLs absolutas `http` o `https`; los valores con otros esquemas quedan como texto o no muestran el enlace de PR. El runner del Ejecutor recibe una lista explícita de variables usadas por `jax/ejecutor`, en lugar de heredar todo el entorno del servicio. La configuración del frontend estático genera una CSP cuyo hash sale de los bytes exactos del script `tema-inicial` del `dist/index.html` que se entrega. La política mantiene `style-src 'self' 'unsafe-inline'` porque el frontend existente usa estilos React inline para datos de facetas, barras y límites; `script-src` permanece restringido a `self` y al hash del tema.

## Por qué

Las fuentes de pipelines y el URL de un PR son datos externos. El proceso de ejecución no necesita secretos de sesión, correo, Telegram ni OAuth de Claude. El script de tema debe correr antes de React sin abrir `unsafe-inline` en CSP.

## Límites y pendiente operativo

La CSP en `ops/frontend/switch.py` cubre únicamente el modo nginx estático opcional, pero su helper puro ahora genera la política de transición con hashes de los bytes exactos de dos `index.html` (publicado viejo y bundle nuevo), y después sólo el nuevo. El sitio público vigente se sirve desde `atem-ai:/www/wwwroot/axioma-ia.io` por aaPanel, según `docs/runbooks/despliegue.md`; no hay un vhost versionado de ese sitio en este repositorio. El runbook define respaldo y restauración verificables de vhost y sitio, `nginx -t`/reload, hash dual antes de copiar el bundle, hash nuevo posterior, HTTP en `/`, `/index.html`, SPA y `/api/health`, más comprobación en navegador. Sigue pendiente ejecutar esa secuencia bajo ventana/GO; este cambio no toca producción. Sacar `FERNET_KEY` o dependencias de JAX del runner requiere cambiar el contrato de capacidades, por lo que no forma parte de este cambio.

## Lecciones y alternativas

Hashar el texto normalizado del script es incorrecto: CSP compara los bytes reales, incluidos saltos y sangrías. Se descartó cambiar de hash antes de publicar el bundle porque bloquearía el tema del `index.html` todavía servido; la política dual es sólo la transición y se reduce al hash nuevo tras verificar la copia. Se descartó `style-src 'self'` porque bloquearía los estilos inline que la UI ya necesita; se conserva el límite estricto sobre scripts. Se descartó heredar el entorno completo por su radio de exposición; la lista blanca conserva las cinco variables de base de datos y el contrato del Ejecutor.

## Rendimiento

No hay consultas ni índices nuevos. No se añadió caché ni trabajo asíncrono. La validación de URL es local y constante por elemento renderizado. No aplica prueba de carga: no se agrega endpoint ni cambia el camino de solicitud; la verificación HTTP de CSP queda pendiente del despliegue autorizado.
