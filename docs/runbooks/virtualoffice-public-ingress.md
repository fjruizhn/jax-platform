# Publicar Virtual Office en su host propio

Este es un artefacto de preparación: no instala nginx, no crea DNS y no
publica un bundle. El objetivo es que `virtualoffice.axioma-ia.io` sirva el SPA
de JAX Platform y hable con su API usando el mismo origen.

## Contrato de red

El vhost modelo está en
[`ops/virtualoffice/virtualoffice.axioma-ia.io.conf`](../../ops/virtualoffice/virtualoffice.axioma-ia.io.conf).
Sirve `dist/` del release inmutable bajo `/opt/jax-office/virtualoffice.axioma-ia.io/current`,
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

   El instalador prepara esa carpeta sin cambiar `current`, nginx ni DNS. El
   SHA esperado y el SHA-256 de `manifest.json` se escriben desde el veredicto
   de auditoría, nunca desde el artefacto que se está instalando. El digest del
   manifiesto ata cada asset y su digest; el validador vuelve a calcularlos
   después de la copia.

   Root no ejecuta estos scripts desde el checkout de JAX: ese checkout puede
   ser modificable por el usuario de la aplicación. Antes de instalar, el
   operador descarga ambos scripts del SHA exacto auditado y los instala,
   compara y verifica bajo un directorio root-owned:

```bash
AUDIT_SHA=SHA_DEL_VEREDICTO
INSTALLER=/usr/local/libexec/jax-office-install-public-release.sh
VALIDATOR=/usr/local/libexec/jax-office-validate-public-ingress.sh
ACTIVATOR=/usr/local/libexec/jax-office-activate-public-release.sh
ROLLBACK=/usr/local/libexec/jax-office-rollback-public-release.sh
LOCKER=/usr/local/libexec/with-office-activation-lock.py
installer_tmp=$(mktemp)
validator_tmp=$(mktemp)
activator_tmp=$(mktemp)
rollback_tmp=$(mktemp)
locker_tmp=$(mktemp)
gh api "repos/fjruizhn/jax-platform/contents/ops/virtualoffice/install-public-release.sh?ref=$AUDIT_SHA" -H 'Accept: application/vnd.github.raw' > "$installer_tmp"
gh api "repos/fjruizhn/jax-platform/contents/ops/virtualoffice/validate-public-ingress.sh?ref=$AUDIT_SHA" -H 'Accept: application/vnd.github.raw' > "$validator_tmp"
gh api "repos/fjruizhn/jax-platform/contents/ops/virtualoffice/activate-public-release.sh?ref=$AUDIT_SHA" -H 'Accept: application/vnd.github.raw' > "$activator_tmp"
gh api "repos/fjruizhn/jax-platform/contents/ops/virtualoffice/rollback-public-release.sh?ref=$AUDIT_SHA" -H 'Accept: application/vnd.github.raw' > "$rollback_tmp"
gh api "repos/fjruizhn/jax-platform/contents/ops/virtualoffice/with-office-activation-lock.py?ref=$AUDIT_SHA" -H 'Accept: application/vnd.github.raw' > "$locker_tmp"
sudo install -d -o root -g root -m 0755 /usr/local/libexec
sudo install -o root -g root -m 0755 "$installer_tmp" "$INSTALLER"
sudo install -o root -g root -m 0755 "$validator_tmp" "$VALIDATOR"
sudo install -o root -g root -m 0755 "$activator_tmp" "$ACTIVATOR"
sudo install -o root -g root -m 0755 "$rollback_tmp" "$ROLLBACK"
sudo install -o root -g root -m 0755 "$locker_tmp" "$LOCKER"
sudo cmp "$installer_tmp" "$INSTALLER"
sudo cmp "$validator_tmp" "$VALIDATOR"
sudo cmp "$activator_tmp" "$ACTIVATOR"
sudo cmp "$rollback_tmp" "$ROLLBACK"
sudo cmp "$locker_tmp" "$LOCKER"
sudo test "$(stat -c '%U:%G:%a' "$INSTALLER")" = root:root:755
sudo test "$(stat -c '%U:%G:%a' "$VALIDATOR")" = root:root:755
sudo test "$(stat -c '%U:%G:%a' "$ACTIVATOR")" = root:root:755
sudo test "$(stat -c '%U:%G:%a' "$ROLLBACK")" = root:root:755
sudo test "$(stat -c '%U:%G:%a' "$LOCKER")" = root:root:755
rm -- "$installer_tmp" "$validator_tmp" "$activator_tmp" "$rollback_tmp" "$locker_tmp"
```

   Después se construye e instala con las copias root-owned:

```bash
SHA=SHA_COMPLETO_AUDITADO
MANIFEST_SHA256=SHA256_DEL_MANIFEST_DESDE_EL_VEREDICTO
BUILD_ROOT=$(mktemp -d)
python3 ops/frontend/build.py --repo /RUTA/AL/REPO --releases "$BUILD_ROOT" --revision "$SHA"
sudo "$INSTALLER" \
  --built-release "$BUILD_ROOT/$SHA" \
  --site-root /opt/jax-office/virtualoffice.axioma-ia.io \
  --releases-root /opt/jax-office/virtualoffice.axioma-ia.io/releases \
  --operational-root /opt \
  --config /RUTA/AL/virtualoffice.axioma-ia.io.conf \
  --validator "$VALIDATOR" --expected-source-sha "$SHA" \
  --expected-manifest-sha256 "$MANIFEST_SHA256" --nginx-user www
rm -rf -- "$BUILD_ROOT"
```

El instalador rechaza un SHA, manifiesto, árbol o enlace que no sea el
artefacto auditado, incluidos sockets, pipes y dispositivos. Exige que todos
los ancestros desde `site-root` hasta `/opt` sean `root` y no tengan
escritura de grupo u otros, antes y después de copiar. Copia a
`releases/.SHA.new`, fija `root:root`, directorios 0755 y archivos 0644, y sólo
entonces renombra el directorio. También ejecuta la atestación `offline` sobre
los bytes ya instalados. Si la atestación falla, no se cambia `current`;
investigar el release conservado antes de intentar otra instalación.
2. Preparar la plantilla root-owned fuera del directorio efectivo de aaPanel,
sin editar ni crear todavía el vhost de `axioma-ia.io`. Ajustar sólo rutas
físicas que difieran de la plantilla y registrar el diff. El activador del paso
4 es quien instala atómicamente ese archivo y por ello puede capturar
correctamente `VHOST_ABSENT` en el primer despliegue.
3. Comprobar bytes y rutas de forma local antes de cargar nginx:

```bash
ops/virtualoffice/validate-public-ingress.sh offline \
  --config /ruta/al/virtualoffice.axioma-ia.io.conf \
  --site-root /opt/jax-office/virtualoffice.axioma-ia.io \
  --releases-root /opt/jax-office/virtualoffice.axioma-ia.io/releases \
  --release /opt/jax-office/virtualoffice.axioma-ia.io/releases/SHA_DE_FUENTE \
  --expected-source-sha SHA_DE_FUENTE_AUDITADO \
  --nginx-user www
```

`offline` no modifica el release. Rechaza un `dist` que tenga archivos distintos
del manifiesto, digests distintos, symlinks internos, o un nombre que no coincida
con el SHA de fuente. Sólo después de pasar se puede apuntar `current` a ese
release; si se verifica `current`, el comando resuelve el enlace una vez y luego
aplica las mismas reglas al destino. El SHA esperado viene del artefacto auditado
independiente, no del nombre del directorio ni del manifiesto. No publicar desde
un checkout Git ni desde `:5173`.

El destino resuelto debe estar bajo `releases-root`; tanto ese árbol como cada
entrada del release son de `root` y no permiten escritura de grupo u otros. El
validador comprueba que el usuario nginx pueda atravesar y leer exactamente los
archivos que servirá. Antes de cambiar `current`, validar el directorio de
release directo; al cambiar, usar un reemplazo atómico del enlace y ejecutar de
inmediato el mismo comando sobre `current`. El comando fija el destino al inicio
y vuelve a resolverlo al terminar, por lo que falla si el enlace cambia durante
la verificación.

Antes de copiar un release, preparar el contenedor una vez como root y conservar
estos modos: `install -d -o root -g root -m 0755 SITE_ROOT`,
`install -d -o root -g root -m 0755 SITE_ROOT/releases`; cada release y su
`dist` quedan `root:root`, directorios 0755 y archivos 0644. `current` es un
enlace creado por root dentro de `SITE_ROOT`; sus directorios padre también son
0755 root:root. No se acepta un `manifest.json` enlazado ni ningún enlace dentro
del release.

4. Activar con el ejecutable root-owned descargado del SHA auditado. Este toma
la instantánea 0700 del vhost y `current` antes de tocar nada, valida el
release directo y a través de `current`, ejecuta `nginx -t`, recarga y hace el
smoke pre-DNS. Un fallo después del cambio restaura automáticamente la
instantánea. No sustituir estos pasos con `ln -s` o una recarga manual.

```bash
sudo /usr/local/libexec/jax-office-activate-public-release.sh \
  --site-root /opt/jax-office/virtualoffice.axioma-ia.io \
  --releases-root /opt/jax-office/virtualoffice.axioma-ia.io/releases \
  --release /opt/jax-office/virtualoffice.axioma-ia.io/releases/SHA_DE_FUENTE \
  --vhost-source /RUTA/A/virtualoffice.axioma-ia.io.conf \
  --vhost-path /RUTA/EFECTIVA/AApanel/virtualoffice.axioma-ia.io.conf \
  --backup-root /var/lib/jax-office/activation-backups \
  --validator /usr/local/libexec/jax-office-validate-public-ingress.sh \
  --nginx /usr/sbin/nginx --expected-source-sha SHA_DE_FUENTE \
  --public-ip INGRESS_PUBLIC_IP --expected-index-sha SHA256_DE_INDEX_HTML \
  --asset-path /assets/ARCHIVO_DEL_MANIFIESTO \
  --expected-asset-sha SHA256_DEL_ASSET --nginx-user www
```

Instalar `jax-office-activate-public-release.sh` y
`jax-office-rollback-public-release.sh` de la misma manera root-owned y
comparada que el instalador y validador del paso 1. Conservar la ruta de la
instantánea que imprime el activador hasta que la publicación quede aceptada.
La instantánea contiene `ACTIVATION_IN_PROGRESS` desde antes del primer cambio;
si queda tras un corte o un `SIGKILL`, no hacer otro despliegue: ejecutar la
reversión con `--recover-incomplete` y esa ruta; el ejecutable exige el
marcador y los bytes previo/nuevo exactos. Conservar `RECOVERY_REQUIRED` si
ella falla.

## Comprobación sin DNS

Una vez que el vhost esté cargado y antes de crear el registro DNS, sustituir
`INGRESS_PUBLIC_IP` y el hash anotado. `--resolve` fuerza el host y SNI correctos
sin depender de DNS público:

```bash
ops/virtualoffice/validate-public-ingress.sh smoke \
  --public-ip INGRESS_PUBLIC_IP \
  --expected-index-sha SHA256_DE_INDEX_HTML \
  --asset-path /assets/ARCHIVO_DEL_MANIFIESTO \
  --expected-asset-sha SHA256_DEL_MISMO_ARCHIVO_EN_MANIFEST
```

La comprobación exige el mismo HTML y confirma que la sesión Office existe
(401 sin credenciales), mientras `/api/admin/users`, `/api/chat` y `/ws/`
dan 404. También exige que `GET /api/apariencia` responda 200 y que su POST
reciba 404. Un 404 en `/api/auth/login` también detiene el proceso. Después se
verifica TLS/SNI desde una red externa y únicamente entonces se crea DNS con el
procedimiento administrado.

El smoke además prueba que los HTML y assets llevan `nosniff`, frame denial,
referrer policy y HSTS; niega `/api/auth/mfa/root-reset`, rutas codificadas,
normalizadas y traversal con `--path-as-is`; y exige que un Host o SNI ajeno
cierre TLS/444 o dé un 400/403/404. Cualquier 2xx, 3xx o 5xx en esas pruebas
detiene el procedimiento.

## Reversión

El activador deja en su instantánea 0700 el vhost previo **o el marcador
`VHOST_ABSENT`**, la ruta canónica de `current` **o el marcador
`CURRENT_ABSENT`**, el manifiesto anterior y el hash del índice nuevo. La
reversión ejecutable usa esos datos: si había vhost/current, los restaura y
repite el smoke con el manifiesto anterior; si era primer despliegue, retira
solamente el vhost y el enlace `current` creados y demuestra que ya no entrega
el hash nuevo. En ambos casos hace `nginx -t` y recarga antes de declarar éxito.

```bash
sudo /usr/local/libexec/jax-office-rollback-public-release.sh \
  --site-root /opt/jax-office/virtualoffice.axioma-ia.io \
  --vhost-path /RUTA/EFECTIVA/AApanel/virtualoffice.axioma-ia.io.conf \
  --backup /var/lib/jax-office/activation-backups/office-activation.XXXXXXXX \
  --nginx /usr/sbin/nginx \
  --validator /usr/local/libexec/jax-office-validate-public-ingress.sh \
  --public-ip INGRESS_PUBLIC_IP --new-index-sha256 SHA256_DEL_INDICE_NUEVO
```

No se borra el release nuevo hasta que la reversión pasó estas comprobaciones.

## Gates que este archivo no reemplaza

Quedan fuera del vhost y bloquean una publicación: auditoría del SHA exacto,
CI verde, pruebas de autorización y MFA, respaldo restaurado, carga medida,
revisión de cookies/origen/CSRF, verificación externa TLS/DNS, y el acto de
producción autorizado.

Además, antes de arrancar el backend con la protección de navegador de
`office-public-auth-hardening`, hay que fijar en su `EnvironmentFile` los
orígenes y hosts explícitos. Los valores operacionales se verifican contra la
topología vigente; el ejemplo de abajo supone que el ingreso privado sigue
siendo `172.16.20.11` y que el origen clásico sigue en `axioma-ia.io`:

```text
JAX_ENVIRONMENT=production
JAX_PUBLIC_HOST_ORIGIN_BINDINGS=axioma-ia.io=https://axioma-ia.io;virtualoffice.axioma-ia.io=https://virtualoffice.axioma-ia.io
JAX_OPERATIONAL_TRUSTED_HOSTS=127.0.0.1,localhost,172.16.20.5
JAX_TRUSTED_PROXIES=172.16.20.11
JAX_REFRESH_COOKIE_SECURE=true
FRONTEND_ORIGIN=https://axioma-ia.io
OFFICE_PUBLIC_ORIGIN=https://virtualoffice.axioma-ia.io
OFFICE_INVITATION_PUBLIC_BASE_URL=https://virtualoffice.axioma-ia.io
```

La configuración de Uvicorn debe confiar sólo en la IP privada del ingreso
(`--proxy-headers --forwarded-allow-ips=172.16.20.11`), conservar un solo
worker y superar su prueba de readiness. Respaldar y probar la restauración del
`EnvironmentFile` y del drop-in de systemd antes de cualquier reinicio.
Comprobar los valores efectivos del servicio: los valores de este ejemplo no
constituyen por sí solos una lectura de producción.
