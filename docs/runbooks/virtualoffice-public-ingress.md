# Publicar Virtual Office en su host propio

Este es un artefacto de preparación: no instala nginx, no crea DNS y no
publica un bundle. El objetivo es que `virtualoffice.axioma-ia.io` sirva el SPA
de JAX Platform y hable con su API usando el mismo origen.

## Contrato de red

El vhost modelo está en
[`ops/virtualoffice/virtualoffice.axioma-ia.io.conf`](../../ops/virtualoffice/virtualoffice.axioma-ia.io.conf).
Sirve un release inmutable bajo `/www/wwwroot/virtualoffice.axioma-ia.io/current`,
usa el certificado wildcard ya instalado para `axioma-ia.io`, y reenvía sólo:

- las rutas de autenticación enumeradas en el archivo;
- `GET /api/apariencia`, la configuración pública y de sólo lectura que carga
  el shell antes de autenticar;
- `/api/virtual-office` y sus hijos.

Todo otro `/api` y cualquier `/ws` devuelve 404 antes de llegar a JAX. La SPA
no es fallback para esas rutas. El proxy conserva `Host`, `X-Real-IP`,
`X-Forwarded-For`, `X-Forwarded-Proto` y `X-Forwarded-Host`; JAX debe confiar
únicamente en la IP privada del ingreso al interpretar los encabezados.

No se configura `default_server` en este artefacto. Antes de instalar hay que
inventariar el vhost que actualmente recibe SNI desconocido en aaPanel y dejar
su respuesta cerrada sin capturar los demás dominios existentes.

## Preparar sin publicar

1. Construir el frontend desde el SHA ya auditado con el mecanismo de releases
   inmutables de `docs/runbooks/frontend-estatico-p100.md`. Copiar el `dist`
   resultante a una carpeta de release nueva, propiedad de `root`, y hacer que
   `current` apunte sólo al release que pasó su manifiesto.
2. Instalar la plantilla como un nuevo archivo de vhost de aaPanel, sin editar
   el vhost de `axioma-ia.io`. Ajustar sólo rutas físicas que difieran de la
   plantilla y registrar el diff.
3. Comprobar bytes y rutas de forma local antes de cargar nginx:

```bash
ops/virtualoffice/validate-public-ingress.sh offline \
  --config /ruta/al/virtualoffice.axioma-ia.io.conf \
  --dist /www/wwwroot/virtualoffice.axioma-ia.io/current \
  --manifest /ruta-de-respaldo/ASSET-SHA256SUMS
```

`offline` no modifica el release: escribe el manifiesto donde indique
`--manifest`. Guardar ese archivo junto con el SHA de fuente y el respaldo, y
anotar el SHA-256 de `index.html`. No publicar desde un checkout Git ni desde
`:5173`.

4. Ejecutar `nginx -t` sobre la configuración completa efectiva de aaPanel,
no sobre el fragmento aislado. Luego recargar sólo después de los gates de
producción y de tener una reversión lista.

```bash
sudo nginx -t
```

## Comprobación sin DNS

Una vez que el vhost esté cargado y antes de crear el registro DNS, sustituir
`INGRESS_PUBLIC_IP` y el hash anotado. `--resolve` fuerza el host y SNI correctos
sin depender de DNS público:

```bash
ops/virtualoffice/validate-public-ingress.sh smoke \
  --public-ip INGRESS_PUBLIC_IP \
  --expected-index-sha SHA256_DE_INDEX_HTML
```

La comprobación exige el mismo HTML y confirma que la sesión Office existe
(401 sin credenciales), mientras `/api/admin/users`, `/api/chat` y `/ws/`
dan 404. También exige que `GET /api/apariencia` responda 200 y que su POST
reciba 404. Un 404 en `/api/auth/login` también detiene el proceso. Después se
verifica TLS/SNI desde una red externa y únicamente entonces se crea DNS con el
procedimiento administrado.

## Reversión

Antes de activar, guardar en un directorio de respaldo con permisos 0700: el
vhost vigente, la ruta canónica previa de `current`, el manifiesto de hashes y
la salida de `nginx -T`. Para volver atrás, restaurar el vhost anterior y
reapuntar `current` al release previamente verificado; validar `nginx -t`,
recargar, y repetir la comprobación `--resolve` con el hash del release previo.
No se borra el release nuevo hasta que la reversión haya pasado la verificación.

## Gates que este archivo no reemplaza

Quedan fuera del vhost y bloquean una publicación: auditoría del SHA exacto,
CI verde, pruebas de autorización y MFA, respaldo restaurado, carga medida,
revisión de cookies/origen/CSRF, verificación externa TLS/DNS, y el acto de
producción autorizado.

Además, antes de emitir invitaciones o enlaces de recuperación hay que fijar
en el entorno del backend, sin subir secretos:

```text
FRONTEND_ORIGIN=https://virtualoffice.axioma-ia.io
OFFICE_INVITATION_PUBLIC_BASE_URL=https://virtualoffice.axioma-ia.io
```

El valor actual de esos ajustes no se infiere de este repositorio. Debe
verificarse en el servicio que atiende JAX antes de cualquier reinicio.
