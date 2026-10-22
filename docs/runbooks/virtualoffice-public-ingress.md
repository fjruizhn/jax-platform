# Publicar Virtual Office en su host propio

Este es un artefacto de preparación: no instala nginx, no crea DNS y no
publica un bundle. El objetivo es que `virtualoffice.axioma-ia.io` sirva el SPA
de JAX Platform y hable con su API usando el mismo origen.

## Contrato de red

El vhost modelo está en
[`ops/virtualoffice/virtualoffice.axioma-ia.io.conf`](../../ops/virtualoffice/virtualoffice.axioma-ia.io.conf).
Sirve un release inmutable bajo `/www/wwwroot/virtualoffice.axioma-ia.io/current`,
usa el certificado wildcard ya instalado para `axioma-ia.io`, habilita HSTS
solamente para este host (`max-age=31536000`, sin `includeSubDomains`), y
reenvía sólo:

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
   y su `manifest.json` resultante a una carpeta de release nueva, propiedad
   de `root`. El directorio de release se llama exactamente como
   `manifest.json.source_sha`; no se genera ni reescribe un manifiesto en el
   ingreso.
2. Instalar la plantilla como un nuevo archivo de vhost de aaPanel, sin editar
   el vhost de `axioma-ia.io`. Ajustar sólo rutas físicas que difieran de la
   plantilla y registrar el diff.
3. Comprobar bytes y rutas de forma local antes de cargar nginx:

```bash
ops/virtualoffice/validate-public-ingress.sh offline \
  --config /ruta/al/virtualoffice.axioma-ia.io.conf \
  --release /www/wwwroot/virtualoffice.axioma-ia.io/releases/SHA_DE_FUENTE
```

`offline` no modifica el release. Rechaza un `dist` que tenga archivos distintos
del manifiesto, digests distintos, symlinks internos, o un nombre que no coincida
con el SHA de fuente. Sólo después de pasar se puede apuntar `current` a ese
release; si se verifica `current`, el comando resuelve el enlace una vez y luego
aplica las mismas reglas al destino. No publicar desde un checkout Git ni desde
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
  --expected-index-sha SHA256_DE_INDEX_HTML \
  --asset-path /assets/ARCHIVO_DEL_MANIFIESTO
```

La comprobación exige el mismo HTML y confirma que la sesión Office existe
(401 sin credenciales), mientras `/api/admin/users`, `/api/chat` y `/ws/`
dan 404. También exige que `GET /api/apariencia` responda 200 y que su POST
reciba 404. Un 404 en `/api/auth/login` también detiene el proceso. Después se
verifica TLS/SNI desde una red externa y únicamente entonces se crea DNS con el
procedimiento administrado.

El smoke además prueba que los HTML y assets llevan `nosniff`, frame denial,
referrer policy y HSTS; niega `/api/auth/mfa/root-reset`, rutas codificadas,
normalizadas y traversal con `--path-as-is`; y exige que un Host o SNI ajeno no
reciba el hash de Office.

## Reversión

Antes de activar, guardar en un directorio de respaldo con permisos 0700: la
salida de `nginx -T`, el vhost previo **o el marcador `VHOST_ABSENT`**, la ruta
canónica de `current` **o el marcador `CURRENT_ABSENT`**, y el manifiesto
original del release anterior. La reversión lee esos marcadores: si había
vhost/current, los restaura; si era el primer despliegue, retira únicamente el
vhost y el enlace `current` que instaló este cambio. En ambos casos ejecuta
`nginx -t`, recarga nginx, y comprueba con `--resolve` que el SNI de Office ya
no sirve su hash; para un estado previo existente, también repite el smoke con
el hash anterior. No se borra el release nuevo hasta que la reversión pasó estas
comprobaciones.

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
